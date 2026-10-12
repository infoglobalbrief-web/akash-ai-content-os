
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

site_url = os.getenv("WORDPRESS_SITE_URL", "").strip().rstrip("/")
username = os.getenv("WORDPRESS_USERNAME", "").strip()
password = os.getenv("WORDPRESS_APPLICATION_PASSWORD", "")

if not site_url or not username or not password:
    raise SystemExit(
        "ERROR: Check WORDPRESS_SITE_URL, WORDPRESS_USERNAME, "
        "and WORDPRESS_APPLICATION_PASSWORD in backend/.env"
    )

if not site_url.startswith("https://"):
    raise SystemExit("ERROR: WORDPRESS_SITE_URL must use HTTPS.")

url = f"{site_url}/wp-json/wp/v2/users/me?context=edit"

try:
    response = httpx.get(
        url,
        auth=(username, password),
        timeout=20.0,
        follow_redirects=False,
    )

    print(f"HTTP status: {response.status_code}")

    if response.is_redirect:
        print("REDIRECT detected.")
        print("Location:", response.headers.get("location", "(unknown)"))

    elif response.status_code == 200:
        data = response.json()
        print("SUCCESS: WordPress authenticated the request.")
        print("WordPress user ID:", data.get("id"))
        print("Assigned roles:", data.get("roles", []))

    else:
        try:
            data = response.json()
            print("WordPress error code:", data.get("code", "unknown"))
            print("WordPress message:", data.get("message", "No message"))
        except ValueError:
            print("Response was not JSON.")
            print("Check the WordPress URL and server configuration.")

except httpx.RequestError as exc:
    print("CONNECTION ERROR:", str(exc))

print("Diagnostic complete. No credentials were printed.")
