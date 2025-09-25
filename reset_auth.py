# reset_auth.py
import os
import pickle
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Scope sirf YouTube upload ke liye
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = "client_secret.json"
TOKEN_PICKLE = Path("token.pickle")

def reset_auth():
    # Purana token delete kar
    if TOKEN_PICKLE.exists():
        os.remove(TOKEN_PICKLE)
        print("🗑️ Deleted old token.pickle")

    # Naya token generate karo
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    # Save kar lo naya token
    with open(TOKEN_PICKLE, "wb") as f:
        pickle.dump(creds, f)

    print("✅ New token.pickle created with correct YouTube upload scope.")

    # Test service
    service = build("youtube", "v3", credentials=creds)
    channels = service.channels().list(part="snippet,contentDetails,statistics", mine=True).execute()
    print("🎉 Authorized for channel:", channels["items"][0]["snippet"]["title"])

if __name__ == "__main__":
    reset_auth()
