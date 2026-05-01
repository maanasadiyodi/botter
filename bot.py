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
WAITING_FOR_DELETE_FILENAME = 2
WAITING_FOR_DOWNLOAD_FILENAME = 3

# ---------- HEALTH CHECK SERVER (keeps Render alive) ----------
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
    """Convert bytes to human readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024:
            return f"{bytes_size:.1f} {unit}"
        bytes_size /= 1024
    return f"{bytes_size:.1f} TB"

def format_progress_bar(progress, length=10):
    """Create a visual progress bar"""
    filled = int(length * progress / 100)
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}] {progress:.1f}%"

# ---------- GOFILE API HELPERS ----------
def get_gofile_account_info():
    """Fetch account storage details"""
    try:
        res = requests.get(f"{GOFILE_API}/accounts", params={"token": GOFILE_TOKEN}).json()
        if res["status"] == "ok":
            return res["data"]
        return None
    except:
        return None

def get_gofile_contents():
    """Fetch all files and folders"""
    try:
        res = requests.get(f"{GOFILE_API}/contents", params={"token": GOFILE_TOKEN}).json()
        if res["status"] == "ok":
            return res["data"]
        return None
    except:
        return None

def build_file_map(data):
    """Build a flat map of filename -> file_id for easy lookup"""
    file_map = {}
    root_id = data["rootFolder"]
    folders = data["contents"].get(root_id, {}).get("children", {})
    for folder_id, folder_data in folders.items():
        files_in_folder = data["contents"].get(folder_id, {}).get("children", {})
        for file_id, file_data in files_in_folder.items():
            file_map[file_data["name"]] = {
                "id": file_id,
                "size": file_data.get("size", 0),
                "folder": folder_data["name"],
                "download_url": file_data.get("link", ""),
                "created": file_data.get("createTime", "Unknown"),
            }
    return file_map

# ---------- COMMAND: /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

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

# ---------- MENU HANDLER ----------
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if not is_authorized(update):
        await query.edit_message_text("⛔ Unauthorized.")
        return

    action = query.data

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

    # --- UPLOAD ---
    elif action == "menu_upload":
        await query.edit_message_text(
            "📤 *Upload File*\n\n"
            "Send me the file you want to upload.\n"
            "Supports: documents, photos, videos, archives, and more.\n\n"
            "Maximum file size: *Unlimited* (Gofile has no limit!)\n\n"
            "Type /cancel to abort.",
            parse_mode="Markdown",
            reply_markup=back_button(),
        )
        return WAITING_FOR_UPLOAD_FILE

    # --- MY FILES ---
    elif action == "menu_files":
        await show_files_menu(update, query)

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
        
        # Show files with download buttons
        keyboard = []
        for fname, finfo in file_map.items():
            size_str = format_size(finfo["size"])
            keyboard.append([
                InlineKeyboardButton(
                    f"📥 {fname} ({size_str})",
                    callback_data=f"download_{fname[:50]}"  # Truncate for callback
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_start")])
        
        # Store file_map with truncated keys for callback lookup
        context.user_data["file_map_truncated"] = {
            f"download_{fname[:50]}": finfo for fname, finfo in file_map.items()
        }
        
        await query.edit_message_text(
            "📥 *Download File*\n\nSelect a file to get the download link:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

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
        
        # Show files with delete buttons
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
            "🗑 *Delete File*\n\n⚠️ This action cannot be undone!\nSelect a file to delete:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

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

    # --- HELP ---
    elif action == "menu_help":
        await query.edit_message_text(
            "❓ *Help & Commands*\n\n"
            "📤 *Upload File* - Send any file to upload it to Gofile\n"
            "📁 *My Files* - Browse and manage your uploaded files\n"
            "📥 *Download File* - Get a direct download link for any file\n"
            "🗑 *Delete File* - Permanently remove a file from Gofile\n"
            "📊 *Storage Info* - View your account storage usage\n\n"
            "💡 *Tips:*\n"
            "• Gofile has *no file size limit*\n"
            "• You can upload any type of file\n"
            "• Upload progress is shown in real-time\n"
            "• File links never expire (with account)",
            parse_mode="Markdown",
            reply_markup=back_button(),
        )

    # --- DOWNLOAD FILE (callback with file key) ---
    elif action.startswith("download_"):
        file_map = context.user_data.get("file_map_truncated", {})
        file_info = file_map.get(action, {})
        
        if file_info:
            download_url = file_info.get("download_url", "Not available")
            await query.edit_message_text(
                f"📥 *Download Link*\n\n"
                f"📄 File: `{file_info.get('name', 'Unknown')}`\n"
                f"📦 Size: `{format_size(file_info.get('size', 0))}`\n"
                f"📁 Folder: `{file_info.get('folder', 'Unknown')}`\n"
                f"📅 Created: `{file_info.get('created', 'Unknown')}`\n\n"
                f"🔗 [Click here to download]({download_url})\n\n"
                f"Or copy this link:\n`{download_url}`",
                parse_mode="Markdown",
                reply_markup=back_button(),
            )
        else:
            await query.edit_message_text("❌ File not found.", reply_markup=back_button())

    # --- CONFIRM DELETE ---
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

    # --- EXECUTE DELETE ---
    elif action == "delete_execute":
        file_info = context.user_data.get("to_delete", {})
        file_id = file_info.get("id")
        file_name = file_info.get("name", "Unknown")
        
        if file_id:
            try:
                res = requests.delete(
                    f"{GOFILE_API}/contents",
                    data={"token": GOFILE_TOKEN, "contentsId": file_id}
                ).json()
                
                if res["status"] == "ok":
                    await query.edit_message_text(
                        f"✅ *Deleted Successfully*\n\n🗑 `{file_name}` has been removed.",
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

    # --- BROWSE FILES (pagination) ---
    elif action.startswith("page_"):
        page = int(action.split("_")[1])
        await show_files_menu(update, query, page)

    return ConversationHandler.END

async def show_files_menu(update, query, page=0):
    """Show files with pagination (10 per page)"""
    data = get_gofile_contents()
    if not data:
        await query.edit_message_text("❌ Failed to fetch files.", reply_markup=back_button())
        return

    file_map = build_file_map(data)
    if not file_map:
        await query.edit_message_text("📭 No files in your account.", reply_markup=back_button())
        return

    files_list = list(file_map.items())
    total_files = len(files_list)
    per_page = 10
    total_pages = (total_files + per_page - 1) // per_page
    start = page * per_page
    end = start + per_page
    page_files = files_list[start:end]

    message = f"📁 *Your Files* (Page {page + 1}/{total_pages})\n\n"
    
    for i, (fname, finfo) in enumerate(page_files, start=start + 1):
        size_str = format_size(finfo["size"])
        message += f"`{i}.` 📄 *{fname}*\n"
        message += f"   └ 💾 `{size_str}` | 📁 `{finfo['folder']}`\n\n"

    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page_{page + 1}"))
    
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_start")])

    await query.edit_message_text(
        message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ---------- FILE UPLOAD HANDLER ----------
async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return ConversationHandler.END

    document = update.message.document
    if not document:
        await update.message.reply_text(
            "❌ Please send a file as a document, not a photo.",
            reply_markup=back_button(),
        )
        return WAITING_FOR_UPLOAD_FILE

    file_name = document.file_name or "unnamed_file"
    file_size = document.file_size
    file_size_str = format_size(file_size)

    # Send initial status message
    status_msg = await update.message.reply_text(
        f"📤 *Starting Upload*\n\n"
        f"📄 File: `{file_name}`\n"
        f"📦 Size: `{file_size_str}`\n"
        f"⏳ Progress: `0%`\n\n"
        f"Please wait...",
        parse_mode="Markdown",
    )

    try:
        # Download from Telegram with progress tracking
        telegram_file = await context.bot.get_file(document.file_id)
        
        # Update progress - downloading from Telegram
        await status_msg.edit_text(
            f"📤 *Uploading to Gofile*\n\n"
            f"📄 File: `{file_name}`\n"
            f"📦 Size: `{file_size_str}`\n"
            f"📥 Downloading from Telegram: `10%`\n\n"
            f"Please wait...",
            parse_mode="Markdown",
        )
        
        file_bytes = await telegram_file.download_as_bytearray()
        
        await status_msg.edit_text(
            f"📤 *Uploading to Gofile*\n\n"
            f"📄 File: `{file_name}`\n"
            f"📦 Size: `{file_size_str}`\n"
            f"☁️ Uploading to Gofile: `30%`\n\n"
            f"Please wait...",
            parse_mode="Markdown",
        )

        # Get server
        server_res = requests.get(f"{GOFILE_API}/servers").json()
        if server_res["status"] != "ok":
            await status_msg.edit_text("❌ Failed to get Gofile server.", reply_markup=back_button())
            return ConversationHandler.END

        server = server_res["data"]["servers"][0]["name"]

        await status_msg.edit_text(
            f"📤 *Uploading to Gofile*\n\n"
            f"📄 File: `{file_name}`\n"
            f"📦 Size: `{file_size_str}`\n"
            f"☁️ Uploading to Gofile: `50%`\n\n"
            f"Please wait...",
            parse_mode="Markdown",
        )

        # Upload to Gofile
        upload_res = requests.post(
            f"https://{server}.gofile.io/uploadFile",
            files={"file": (file_name, bytes(file_bytes))},
            data={"token": GOFILE_TOKEN},
        ).json()

        await status_msg.edit_text(
            f"📤 *Uploading to Gofile*\n\n"
            f"📄 File: `{file_name}`\n"
            f"📦 Size: `{file_size_str}`\n"
            f"✅ Finalizing: `90%`\n\n"
            f"Almost done...",
            parse_mode="Markdown",
        )

        if upload_res["status"] == "ok":
            file_data = upload_res["data"]
            download_link = file_data["downloadPage"]
            
            await status_msg.edit_text(
                f"✅ *Upload Complete!*\n\n"
                f"📄 Name: `{file_data['fileName']}`\n"
                f"📦 Size: `{file_size_str}`\n"
                f"🆔 ID: `{file_data['fileId']}`\n\n"
                f"🔗 *Download Link:*\n[Click here]({download_link})\n\n"
                f"📋 Or copy:\n`{download_link}`",
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
    # Start health check server in background thread
    threading.Thread(target=run_health_server, daemon=True).start()

    # Build Telegram bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Upload conversation
    upload_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_handler, pattern="^menu_upload$")],
        states={
            WAITING_FOR_UPLOAD_FILE: [
                MessageHandler(filters.Document.ALL, handle_upload),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text(
                    "❌ Please send a file as a document.", reply_markup=back_button()
                )),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Main handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(upload_conv)
    app.add_handler(CallbackQueryHandler(menu_handler))  # Catches all button presses

    print("✅ Bot is running with button interface...")
    app.run_polling()

if __name__ == "__main__":
    main()
