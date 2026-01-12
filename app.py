import os
import urllib.parse

import magic
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request
from werkzeug.utils import secure_filename
from google_recaptcha_flask import ReCaptcha

load_dotenv()

app = Flask(__name__)

# -------------------------
# reCAPTCHA (test keys)
# -------------------------
RECAPTCHA_SITE_KEY = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
RECAPTCHA_SECRET_KEY = "6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe"

recaptcha = ReCaptcha(app)
app.config.update(
    dict(
        GOOGLE_RECAPTCHA_ENABLED=True,
        GOOGLE_RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY,
        GOOGLE_RECAPTCHA_SECRET_KEY=RECAPTCHA_SECRET_KEY,
        GOOGLE_RECAPTCHA_LANGUAGE="en",
    )
)
recaptcha.init_app(app)


ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN")
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN")

# Zendesk endpoints
ZENDESK_REQUESTS_URL = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/requests.json"
ZENDESK_UPLOADS_URL = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/uploads.json"

# What we accept from users
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "mp4"}
ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "video/mp4"}


def get_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def detect_real_mime_from_bytes(file_bytes: bytes) -> str:
    # Zendesk cares about the real file signature ("magic bytes")
    return magic.from_buffer(file_bytes[:4096], mime=True)


def create_zendesk_request(payload: dict):
    """
    Creates a Zendesk request (ticket).
    """
    auth = (f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN)
    headers = {"Content-Type": "application/json"}
    return requests.post(
        ZENDESK_REQUESTS_URL,
        json=payload,
        auth=auth,
        headers=headers,
        timeout=20
    )

def zendesk_upload_file_bytes(filename: str, file_bytes: bytes, mime_type: str):
    """
    Uploads a file to Zendesk using the correct format:
    - raw bytes in request body (data=...)
    - Content-Type set to the file's MIME type
    Zendesk returns an upload token which you attach to the ticket.
    """
    auth = (f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN)

    safe_name = secure_filename(filename)

    # Optional: normalize .jpeg -> .jpg for safety
    base, ext = os.path.splitext(safe_name)
    if ext.lower() == ".jpeg":
        safe_name = base + ".jpg"

    encoded_name = urllib.parse.quote(safe_name)
    upload_url = f"{ZENDESK_UPLOADS_URL}?filename={encoded_name}"

    headers = {"Content-Type": mime_type}

    resp = requests.post(
        upload_url,
        auth=auth,
        data=file_bytes,     # ✅ raw bytes (NOT multipart)
        headers=headers,
        timeout=60
    )
    return resp


@app.get("/")
def landing():
    return render_template("landing.html")


# -------------------------
# AV SUPPORT (existing)
# -------------------------
@app.get("/forms/av-support")
def av_form():
    return render_template("av_form.html")


@app.post("/forms/av-support")
def submit_av_form():
    if not recaptcha.verify():
        return "reCAPTCHA verification failed. Please try again.", 400

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    building = request.form.get("building", "").strip()
    room = request.form.get("room", "").strip()
    date_needed = request.form.get("date_needed", "").strip()

    if not (name and email and subject and description):
        return "Missing required fields (name, email, subject, description).", 400

    formatted_body = (
        f"{description}\n\n"
        f"---\n"
        f"Portal Form: AV Support\n"
        f"Location: {building} {room}\n"
        f"Date Needed: {date_needed}\n"
        f"Requester: {name} ({email})\n"
        f"Tag: portal_demo_av\n"
    )

    payload = {
        "request": {
            "subject": subject,
            "comment": {"body": formatted_body},
            "requester": {"name": name, "email": email},
            "tags": ["portal_demo_av"]
        }
    }

    resp = create_zendesk_request(payload)

    if 200 <= resp.status_code < 300:
        data = resp.json()
        request_id = data.get("request", {}).get("id")
        return render_template("success.html", request_id=request_id)
    else:
        return f"Zendesk API error {resp.status_code}: {resp.text}", 500


# -------------------------
# DIGITAL SIGNAGE (new)
# -------------------------
@app.get("/forms/digital-signage")
def digital_signage_form():
    return render_template("digital_signage_form.html")


@app.post("/forms/digital-signage")
def submit_digital_signage():
    from flask import abort
    if not recaptcha.verify():
        return "reCAPTCHA verification failed. Please try again.", 400

    # Required fields
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    department = request.form.get("department", "").strip()

    # Optional fields
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    notes = request.form.get("notes", "").strip()

    # File upload field
    file_obj = request.files.get("signage_file")

    # Validate required fields
    if not (name and email and department):
        return "Missing required fields (name, email, department/club).", 400

    if not file_obj or file_obj.filename == "":
        return "Missing required file upload (.png, .jpg/.jpeg, .mp4).", 400

    filename = file_obj.filename
    ext = get_extension(filename)

    if ext not in ALLOWED_EXTENSIONS:
        return "Invalid file type. Allowed: .png, .jpg/.jpeg, .mp4", 400

    # Read file bytes ONCE
    file_bytes = file_obj.read()

    # Detect what the file REALLY is
    real_mime = detect_real_mime_from_bytes(file_bytes)

    if real_mime not in ALLOWED_MIME_TYPES:
        return (
            f"Invalid file format detected: {real_mime}. "
            "Please upload a true PNG, JPG, or MP4 file (not PDF, slideshow, or WebP). "
            "Tip: If your file came from Canva or Google, re-export it as PNG or JPG.",
            400
        )

    # Debug prints (keep for now; remove later)
    print("Filename:", filename)
    print("Extension:", ext)
    print("Real MIME:", real_mime)

    # ✅ Upload to Zendesk correctly (raw bytes)
    upload_resp = zendesk_upload_file_bytes(filename, file_bytes, real_mime)

    if not (200 <= upload_resp.status_code < 300):
        return f"Zendesk Upload API error {upload_resp.status_code}: {upload_resp.text}", 500

    upload_data = upload_resp.json()
    upload_token = upload_data.get("upload", {}).get("token")

    if not upload_token:
        return "Upload succeeded but no upload token was returned by Zendesk.", 500

    # Make a clean ticket body
    formatted_body = (
        "Digital Signage Request\n"
        "----------------------\n"
        f"Requester: {name} ({email})\n"
        f"Tandon Department/Club: {department}\n"
        f"Start Date: {start_date or 'Run immediately (blank)'}\n"
        f"End Date: {end_date or 'Take down day after event (blank)'}\n"
        f"Additional Notes: {notes or '(none)'}\n"
        "\n"
        "Attachment: uploaded via portal\n"
        "Tag: portal_digital_signage\n"
    )

    payload = {
        "request": {
            "subject": f"Digital Signage Request - {department}",
            "comment": {
                "body": formatted_body,
                "uploads": [upload_token]
            },
            "requester": {"name": name, "email": email},
            "tags": ["portal_digital_signage"]
        }
    }

    resp = create_zendesk_request(payload)

    if 200 <= resp.status_code < 300:
        data = resp.json()
        request_id = data.get("request", {}).get("id")
        return render_template("success.html", request_id=request_id)
    else:
        return f"Zendesk Requests API error {resp.status_code}: {resp.text}", 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
