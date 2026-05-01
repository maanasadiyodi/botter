import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)

# ---------- CONFIG ----------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GOFILE_TOKEN = os.getenv("GOFILE_TOKEN")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))
PORT = int(os.getenv("PORT", "10000"))
GOFILE_API = "https://api.gofile.io"

# Conversation states
WAITING_FOR_UPLOAD_FILE = 1

# ---------- HEALTH CHECK SERVER ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"🔌 Health server listening on port {PORT}")
    server.serve_forever()

# ---------- SECURITY ----------
def is_authorized(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_USER_ID

# ---------- KEYBOARDS ----------
def main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("📤 Upload File", callback_data="menu_upload")],
        [InlineKeyboardButton("📁 My Files", callback_data="menu_files")],
        [InlineKeyboardButton("📥 Download File", callback_data="menu_download")],
        [InlineKeyboardButton("🗑 Delete File", callback_data="menu_delete")],
        [InlineKeyboardButton("📊 Storage Info", callback_data="menu_storage")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help")],
    ]
    return InlineKeyboardMarkup(keyboard)

def back_button():
    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_start")]]
    return InlineKeyboardMarkup(keyboard)

# ---------- FORMAT HELPERS ----------
def format_size(bytes_size):
    try:
        b = int(bytes_size)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if b < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"
    except:
        return "Unknown"

def format_progress_bar(progress, length=10):
    filled = int(length * progress / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {progress:.1f}%"

# ---------- GOFILE API HELPERS (CORRECTED ENDPOINTS) ----------
def get_gofile_account_info():
    """Fetch account storage details"""
    try:
        res = requests.get(f"{GOFILE_API}/account", params={"token": GOFILE_TOKEN}).json()
        print(f"DEBUG /account response: {res}")  # Log for debugging
        if res["status"] == "ok":
            return res["data"]
        return None
    except Exception as e:
        print(f"ERROR get_gofile_account_info: {e}")
        return None

def get_gofile_contents():
    """Fetch all files and folders using corrected endpoint"""
    try:
        # Use the correct /listFiles endpoint
        res = requests.get(f"{GOFILE_API}/listFiles", params={"token": GOFILE_TOKEN}).json()
        print(f"DEBUG /listFiles response status: {res.get('status')}")
        
        if res["status"] == "ok":
            return res["data"]
        else:
            print(f"ERROR: Gofile API returned status: {res.get('status')}, message: {res.get('message')}")
            return None
    except Exception as e:
        print(f"ERROR get_gofile_contents: {e}")
        return None

def build_file_map(data):
    """Build a flat map of filename -> file_id"""
    file_map = {}
    # The /listFiles endpoint returns a different structure
    # It has "files" directly, not a nested folder structure
    files = data.get("files", [])
    
    for file_data in files:
        name = file_data.get("name", "unnamed")
        file_map[name] = {
            "id": file_data.get("fileId") or file_data.get("id", ""),
            "size": file_data.get("size", 0),
            "download_url": file_data.get("link", ""),
            "created": file_data.get("createTime", "Unknown"),
        }
    
    return file_map

# ---------- COMMAND: /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return ConversationHandler.END

    account = get_gofile_account_info()
    storage_used = format_size(account.get("storageUsed", 0)) if account else "Unknown"
    storage_total = format_size(account.get("storageTotal", 0)) if account else "Unknown"

    await update.message.reply_text(
        f"👋 *Welcome to Gofile Manager!*\n\n"
        f"📊 Storage: `{storage_used}` / `{storage_total}`\n\n"
        f"Choose an action:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ---------- MENU HANDLER (FIXED) ----------
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_authorized(update):
        await query.edit_message_text("⛔ Unauthorized.")
        return ConversationHandler.END

    action = query.data
    print(f"DEBUG menu action: {action}")  # Log for debugging

    # --- MAIN MENU ---
    if action == "menu_start":
        account = get_gofile_account_info()
        storage_used = format_size(account.get("storageUsed", 0)) if account else "Unknown"
        storage_total = format_size(account.get("storageTotal", 0)) if account else "Unknown"
        await query.edit_message_text(
            f"👋 *Welcome to Gofile Manager!*\n\n"
            f"📊 Storage: `{storage_used}` / `{storage_total}`\n\n"
            f"Choose an action:",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )
        return ConversationHandler.END

    # --- UPLOAD ---
    elif action == "menu_upload":
        await query.edit_message_text(
            "📤 *Upload File*\n\n"
            "Send me the file you want to upload.\n"
            "Maximum file size: *Unlimited*\n\n"
            "Type /cancel to abort.",
            parse_mode="Markdown",
            reply_markup=back_button(),
        )
        return WAITING_FOR_UPLOAD_FILE

    # --- MY FILES ---
    elif action == "menu_files":
        data = get_gofile_contents()
        if not data:
            await query.edit_message_text(
                "❌ Failed to fetch files. Check your Gofile token or account.",
                reply_markup=back_button()
            )
            return ConversationHandler.END

        file_map = build_file_map(data)
        if not file_map:
            await query.edit_message_text(
                "📭 No files in your account yet.\nUse 📤 Upload to add files!",
                reply_markup=back_button()
            )
            return ConversationHandler.END

        message = "📁 *Your Files*\n\n"
        for fname, finfo in file_map.items():
            size_str = format_size(finfo["size"])
            message += f"📄 *{fname}*\n   └ 💾 `{size_str}`\n\n"

        await query.edit_message_text(
            message,
            parse_mode="Markdown",
            reply_markup=back_button()
        )
        return ConversationHandler.END

    # --- DOWNLOAD ---
    elif action == "menu_download":
        data = get_gofile_contents()
        if not data:
            await query.edit_message_text("❌ Failed to fetch files.", reply_markup=back_button())
            return ConversationHandler.END

        file_map = build_file_map(data)
        if not file_map:
            await query.edit_message_text("📭 No files found.", reply_markup=back_button())
            return ConversationHandler.END

        context.user_data["file_map"] = file_map
        
        keyboard = []
        for fname, finfo in file_map.items():
            size_str = format_size(finfo["size"])
            keyboard.append([
                InlineKeyboardButton(
                    f"📥 {fname} ({size_str})",
                    callback_data=f"download_{fname[:50]}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_start")])
        
        context.user_data["file_map_truncated"] = {
            f"download_{fname[:50]}": finfo for fname, finfo in file_map.items()
        }
        
        await query.edit_message_text(
            "📥 *Download File*\n\nSelect a file to get the download link:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

    # --- DELETE ---
    elif action == "menu_delete":
        data = get_gofile_contents()
        if not data:
            await query.edit_message_text("❌ Failed to fetch files.", reply_markup=back_button())
            return ConversationHandler.END

        file_map = build_file_map(data)
        if not file_map:
            await query.edit_message_text("📭 No files to delete.", reply_markup=back_button())
            return ConversationHandler.END

        context.user_data["file_map_delete"] = file_map
        
        keyboard = []
        for fname, finfo in file_map.items():
            size_str = format_size(finfo["size"])
            keyboard.append([
                InlineKeyboardButton(
                    f"🗑 {fname} ({size_str})",
                    callback_data=f"deleteconfirm_{fname[:50]}"
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_start")])
        
        context.user_data["file_map_delete_truncated"] = {
            f"deleteconfirm_{fname[:50]}": finfo for fname, finfo in file_map.items()
        }
        
        await query.edit_message_text(
            "🗑 *Delete File*\n\n⚠️ This cannot be undone!\nSelect a file to delete:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return ConversationHandler.END

    # --- STORAGE INFO ---
    elif action == "menu_storage":
        account = get_gofile_account_info()
        if account:
            used = account.get("storageUsed", 0)
            total = account.get("storageTotal", 0)
            percent = (used / total * 100) if total > 0 else 0
            bar = format_progress_bar(percent)
            
            await query.edit_message_text(
                f"📊 *Storage Information*\n\n"
                f"💾 Used: `{format_size(used)}`\n"
                f"💿 Total: `{format_size(total)}`\n"
                f"🔋 Free: `{format_size(total - used)}`\n\n"
                f"{bar}\n\n"
                f"📁 Files: `{account.get('fileCount', 'N/A')}`\n"
                f"📂 Folders: `{account.get('folderCount', 'N/A')}`",
                parse_mode="Markdown",
                reply_markup=back_button(),
            )
        else:
            await query.edit_message_text("❌ Failed to fetch storage info.", reply_markup=back_button())
        return ConversationHandler.END

    # --- HELP ---
    elif action == "menu_help":
        await query.edit_message_text(
            "❓ *Help & Commands*\n\n"
            "📤 *Upload File* - Send any file to upload\n"
            "📁 *My Files* - Browse your files\n"
            "📥 *Download File* - Get download link\n"
            "🗑 *Delete File* - Remove a file\n"
            "📊 *Storage Info* - View usage\n\n"
            "💡 Gofile has *no file size limit*!",
            parse_mode="Markdown",
            reply_markup=back_button(),
        )
        return ConversationHandler.END

    # --- DOWNLOAD EXECUTE ---
    elif action.startswith("download_"):
        file_map = context.user_data.get("file_map_truncated", {})
        file_info = file_map.get(action, {})
        
        if file_info:
            download_url = file_info.get("download_url", "Not available")
            file_name = file_info.get("name", "Unknown")
            await query.edit_message_text(
                f"📥 *Download Link*\n\n"
                f"📄 File: `{file_name}`\n"
                f"📦 Size: `{format_size(file_info.get('size', 0))}`\n\n"
                f"🔗 [Click here to download]({download_url})\n\n"
                f"Or copy:\n`{download_url}`",
                parse_mode="Markdown",
                reply_markup=back_button(),
            )
        else:
            await query.edit_message_text("❌ File not found.", reply_markup=back_button())
        return ConversationHandler.END

    # --- DELETE CONFIRM ---
    elif action.startswith("deleteconfirm_"):
        file_map = context.user_data.get("file_map_delete_truncated", {})
        file_info = file_map.get(action, {})
        
        if file_info:
            context.user_data["to_delete"] = file_info
            keyboard = [
                [
                    InlineKeyboardButton("✅ Yes, Delete", callback_data="delete_execute"),
                    InlineKeyboardButton("❌ Cancel", callback_data="menu_delete"),
                ]
            ]
            await query.edit_message_text(
                f"⚠️ *Confirm Deletion*\n\n"
                f"📄 File: `{file_info.get('name', 'Unknown')}`\n"
                f"📦 Size: `{format_size(file_info.get('size', 0))}`\n\n"
                f"Are you sure? This *cannot* be undone!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await query.edit_message_text("❌ File not found.", reply_markup=back_button())
        return ConversationHandler.END

    # --- DELETE EXECUTE ---
    elif action == "delete_execute":
        file_info = context.user_data.get("to_delete", {})
        file_id = file_info.get("id")
        
        if file_id:
            try:
                # Corrected: DELETE /deleteFile instead of /contents
                res = requests.delete(
                    f"{GOFILE_API}/deleteFile",
                    data={"token": GOFILE_TOKEN, "fileId": file_id}
                ).json()
                
                print(f"DEBUG delete response: {res}")  # Log for debugging
                
                if res["status"] == "ok":
                    await query.edit_message_text(
                        f"✅ *Deleted Successfully!*\n\n🗑 File has been removed.",
                        parse_mode="Markdown",
                        reply_markup=back_button(),
                    )
                else:
                    await query.edit_message_text(
                        f"❌ Delete failed: {res.get('message', 'Unknown error')}",
                        reply_markup=back_button(),
                    )
            except Exception as e:
                await query.edit_message_text(f"❌ Error: {str(e)}", reply_markup=back_button())
        else:
            await query.edit_message_text("❌ No file selected.", reply_markup=back_button())
        return ConversationHandler.END

    # --- PAGINATION (if you add it back later) ---
    elif action.startswith("page_"):
        page = int(action.split("_")[1])
        # Simplified: just show files again
        data = get_gofile_contents()
        if data:
            file_map = build_file_map(data)
            message = "📁 *Your Files*\n\n"
            for fname, finfo in file_map.items():
                message += f"📄 *{fname}*\n   └ 💾 `{format_size(finfo['size'])}`\n\n"
            await query.edit_message_text(message, parse_mode="Markdown", reply_markup=back_button())
        return ConversationHandler.END

    return ConversationHandler.END

# ---------- FILE UPLOAD HANDLER ----------
async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return ConversationHandler.END

    document = update.message.document
    if not document:
        await update.message.reply_text(
            "❌ Please send a file as a document.",
            reply_markup=back_button(),
        )
        return WAITING_FOR_UPLOAD_FILE

    file_name = document.file_name or "unnamed_file"
    file_size = document.file_size
    file_size_str = format_size(file_size)

    status_msg = await update.message.reply_text(
        f"📤 *Uploading...*\n\n"
        f"📄 File: `{file_name}`\n"
        f"📦 Size: `{file_size_str}`\n\n"
        f"⏳ Please wait...",
        parse_mode="Markdown",
    )

    try:
        # Download from Telegram
        telegram_file = await context.bot.get_file(document.file_id)
        file_bytes = await telegram_file.download_as_bytearray()
        
        await status_msg.edit_text(
            f"📤 *Uploading to Gofile...*\n\n"
            f"📄 File: `{file_name}`\n"
            f"📦 Size: `{file_size_str}`\n"
            f"☁️ Sending to Gofile...",
            parse_mode="Markdown",
        )

        # Get server
        server_res = requests.get(f"{GOFILE_API}/servers").json()
        if server_res["status"] != "ok":
            await status_msg.edit_text("❌ Failed to get Gofile server.", reply_markup=back_button())
            return ConversationHandler.END

        server = server_res["data"]["servers"][0]["name"]

        # Upload to Gofile
        upload_res = requests.post(
            f"https://{server}.gofile.io/uploadFile",
            files={"file": (file_name, bytes(file_bytes))},
            data={"token": GOFILE_TOKEN},
        ).json()

        print(f"DEBUG upload response: {upload_res}")  # Log for debugging

        if upload_res["status"] == "ok":
            file_data = upload_res["data"]
            download_link = file_data.get("downloadPage", file_data.get("link", "No link"))
            
            await status_msg.edit_text(
                f"✅ *Upload Complete!*\n\n"
                f"📄 Name: `{file_data.get('fileName', file_name)}`\n"
                f"📦 Size: `{file_size_str}`\n\n"
                f"🔗 [Download Link]({download_link})\n\n"
                f"`{download_link}`",
                parse_mode="Markdown",
                reply_markup=back_button(),
            )
        else:
            await status_msg.edit_text(
                f"❌ Upload failed: {upload_res.get('message', 'Unknown error')}",
                reply_markup=back_button(),
            )

    except Exception as e:
        await status_msg.edit_text(
            f"❌ Error: {str(e)}",
            reply_markup=back_button(),
        )

    return ConversationHandler.END

# ---------- CANCEL ----------
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return ConversationHandler.END
    await update.message.reply_text(
        "❌ Operation cancelled.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ---------- MAIN ----------
def main():
    # Start health check server in background
    threading.Thread(target=run_health_server, daemon=True).start()

    # Build Telegram bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Simplified ConversationHandler — only for upload flow
    upload_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_upload$")],
        states={
            WAITING_FOR_UPLOAD_FILE: [
                MessageHandler(filters.Document.ALL, handle_upload),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Main handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(upload_conv)
    
    # All other button presses go to menu_handler
    app.add_handler(CallbackQueryHandler(menu_handler))

    print("✅ Bot is running with button interface...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
