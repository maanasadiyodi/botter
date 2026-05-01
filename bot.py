import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOFILE_TOKEN = os.getenv("GOFILE_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
GOFILE_API = "https://api.gofile.io"

# Conversation states
WAITING_FOR_FILE = 1
WAITING_FOR_DELETE = 2

# ---------- SIMPLE HEALTH CHECK SERVER ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"🔌 Health server listening on port {PORT}")
    server.serve_forever()

# ---------- SECURITY CHECK ----------
def is_authorized(update: Update) -> bool:
    user_id = update.effective_user.id
    return user_id == ALLOWED_USER_ID

# ---------- COMMANDS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    await update.message.reply_text(
        "👋 Welcome! Here's what I can do:\n\n"
        "/upload - Upload a file to Gofile\n"
        "/files - List your Gofile files\n"
        "/delete - Delete a file from Gofile\n"
        "/help - Show this message"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    await update.message.reply_text(
        "📋 *Available Commands*\n\n"
        "🔹 /upload - Send a file and I'll upload it\n"
        "🔹 /files - View all your stored files\n"
        "🔹 /delete - Delete a file by its name or ID\n"
        "🔹 /help - Show this help message",
        parse_mode="Markdown"
    )

# ---------- UPLOAD FLOW ----------
async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return ConversationHandler.END
    await update.message.reply_text("📤 Send me the file you want to upload:")
    return WAITING_FOR_FILE

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = update.message.document or update.message.photo[-1] if update.message.photo else None
    
    if not file and update.message.document:
        file = update.message.document
    elif not file:
        file = update.message.effective_attachment
        if hasattr(file, 'file_id'):
            file = file
        else:
            await update.message.reply_text("❌ Please send a valid file.")
            return WAITING_FOR_FILE
    
    telegram_file = await context.bot.get_file(update.message.document.file_id)
    file_bytes = await telegram_file.download_as_bytearray()
    file_name = update.message.document.file_name or "unnamed_file"
    
    await update.message.reply_text("⏳ Uploading to Gofile...")
    
    try:
        server_res = requests.get(f"{GOFILE_API}/servers").json()
        if server_res["status"] != "ok":
            await update.message.reply_text("❌ Failed to get Gofile server.")
            return ConversationHandler.END
        
        server = server_res["data"]["servers"][0]["name"]
        
        upload_res = requests.post(
            f"https://{server}.gofile.io/uploadFile",
            files={"file": (file_name, bytes(file_bytes))},
            data={"token": GOFILE_TOKEN}
        ).json()
        
        if upload_res["status"] == "ok":
            file_data = upload_res["data"]
            download_link = file_data["downloadPage"]
            await update.message.reply_text(
                f"✅ *File uploaded!*\n\n"
                f"📄 Name: `{file_data['fileName']}`\n"
                f"🔗 Link: {download_link}",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(f"❌ Upload failed: {upload_res}")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# ---------- LIST FILES ----------
async def list_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return
    
    try:
        res = requests.get(f"{GOFILE_API}/contents", params={"token": GOFILE_TOKEN}).json()
        
        if res["status"] != "ok":
            await update.message.reply_text("❌ Failed to fetch files.")
            return
        
        data = res["data"]
        root_id = data["rootFolder"]
        folders = data["contents"].get(root_id, {}).get("children", {})
        
        if not folders:
            await update.message.reply_text("📭 Your Gofile account is empty.")
            return
        
        message = "📁 *Your Gofile Files*\n\n"
        
        for folder_id, folder_data in folders.items():
            message += f"📂 *{folder_data['name']}*\n"
            files_in_folder = data["contents"].get(folder_id, {}).get("children", {})
            
            for file_id, file_data in files_in_folder.items():
                message += f"  📄 `{file_data['name']}`\n"
            message += "\n"
        
        await update.message.reply_text(message, parse_mode="Markdown")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

# ---------- DELETE FLOW ----------
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return ConversationHandler.END
    
    try:
        res = requests.get(f"{GOFILE_API}/contents", params={"token": GOFILE_TOKEN}).json()
        data = res["data"]
        root_id = data["rootFolder"]
        folders = data["contents"].get(root_id, {}).get("children", {})
        
        if not folders:
            await update.message.reply_text("📭 No files to delete.")
            return ConversationHandler.END
        
        file_list = ""
        context.user_data["gofile_data"] = data
        
        for folder_id, folder_data in folders.items():
            files_in_folder = data["contents"].get(folder_id, {}).get("children", {})
            for file_id, file_data in files_in_folder.items():
                file_list += f"• `{file_data['name']}`\n"
                context.user_data["file_map"] = context.user_data.get("file_map", {})
                context.user_data["file_map"][file_data["name"]] = file_id
        
        await update.message.reply_text(
            f"{file_list}\n\nType the *exact filename* you want to delete:",
            parse_mode="Markdown"
        )
        return WAITING_FOR_DELETE
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
        return ConversationHandler.END

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_name = update.message.text.strip()
    file_map = context.user_data.get("file_map", {})
    
    if file_name not in file_map:
        await update.message.reply_text("❌ File not found. Try again or /cancel.")
        return WAITING_FOR_DELETE
    
    file_id = file_map[file_name]
    
    try:
        res = requests.delete(
            f"{GOFILE_API}/contents",
            data={"token": GOFILE_TOKEN, "contentsId": file_id}
        ).json()
        
        if res["status"] == "ok":
            await update.message.reply_text(f"🗑 Deleted: `{file_name}`", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Delete failed: {res}")
    
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")
    
    return ConversationHandler.END

# ---------- MAIN ----------
def main():
    # Start the health check server in a separate thread
    threading.Thread(target=run_health_server, daemon=True).start()
    
    # Start the Telegram bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    upload_conv = ConversationHandler(
        entry_points=[CommandHandler("upload", upload_start)],
        states={
            WAITING_FOR_FILE: [MessageHandler(filters.Document.ALL, receive_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    delete_conv = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_start)],
        states={
            WAITING_FOR_DELETE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_delete)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("files", list_files))
    app.add_handler(upload_conv)
    app.add_handler(delete_conv)
    
    print("✅ Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
