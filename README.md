# Prosciutto

A web app for viewing unified mailboxes across Gmail accounts.

## Features

- **Multi-Account Support**: Add multiple Google accounts via OAuth2.
- **Unified Inbox**: View an aggregate list of emails from all connected accounts.
- **Individual Inbox View**: View emails for a specific account.
- **Message Detail Panel**: Polished side panel that opens when selecting a message, with support for full HTML rendering and plain text fallbacks.
- **Compose & Send**: Send emails from any of the connected accounts.
- **Autocomplete & Contacts**: Smart recipient auto-completion in the composer, drawing from a 30-day warm-up of your "Sent" folder (Recents) and synced Google Contacts (via the People API). Includes settings to manage or clear this cache.
- **System Statistics**: Monitor your connected accounts, database size, cache usage, and synced contact counts directly from the Advanced settings.
- **Interactive GCP Scripts**: Easy project setup, API management, and deployment.
- **Mock Tests**: Comprehensive test suite with Google API mocks.
- **GitHub Actions**: Continuous integration with automated tests on every push.

## Technical Architecture

- **Backend**: Python 3.11 with [FastAPI](https://fastapi.tiangolo.com/).
  - **Database**: [SQLModel](https://sqlmodel.tiangolo.com/) (SQLAlchemy + Pydantic) with SQLite for local storage of settings, account tokens, and contact caches.
  - **Caching**: [DiskCache](http://www.grantjenks.com/docs/diskcache/) for improved performance when fetching message details.
  - **Google APIs**: Integrated via `google-api-python-client` using the Gmail API (for mail) and the People API (for contacts) with an OAuth2 flow.
- **Frontend**: Vanilla HTML5, CSS3, and JavaScript.
  - **UI**: Modern design with responsive sidebar and multi-tab settings modal.
  - **Icons**: [Font Awesome 6](https://fontawesome.com/).
- **CI/CD**: GitHub Actions for automated testing.

## API Endpoints

### Authentication
- `GET /auth/login`: Initiates OAuth2 flow.
- `GET /auth/callback`: Handles Google OAuth2 callback and stores credentials.

### Accounts & Settings
- `GET /accounts`: List all connected accounts.
- `DELETE /accounts/{id}`: Remove a connected account.
- `GET /settings`: Get current app settings (GCP credentials, theme, etc.).
- `POST /settings`: Update app settings.
- `GET /stats`: Retrieve system statistics (database size, cache size, contact counts).

### Gmail Operations
- `GET /unified/messages`: Unified inbox across all accounts.
- `GET /accounts/{id}/messages`: List messages for a specific account.
- `GET /accounts/{id}/messages/{msg_id}`: Get full details of a specific message.
- `GET /accounts/{id}/search`: Search mail within an account.
- `POST /accounts/{id}/send`: Compose and send an email.

### Contacts & Autocomplete
- `GET /autocomplete`: Fetch ranked autocomplete suggestions for the composer (searches Recents and Google Contacts).
- `GET /accounts/{account_id}/sync-contacts`: Trigger a background delta-sync of Google Contacts.
- `POST /contacts/clear`: Clear the local contacts and recents cache, forcing a full re-sync.

## Prerequisites

- Python 3.9+
- Google Cloud SDK (`gcloud`)
- Docker (optional, for containerized deployment)

## Getting Started

### 1. GCP Setup

Use the interactive script to set up your Google Cloud project and enable necessary APIs (Gmail and People APIs):

```bash
./gcp_setup.sh
```

Choose **Option 1** to create a project and enable the APIs. Follow the instructions to create OAuth2 credentials in the Google Cloud Console. Then, use **Option 2** to save your credentials to `.env`.

### 2. Local Setup (Without Docker)

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

### 3. Running the App

#### Using standard Python:
Start the FastAPI server from the root directory:

```bash
export PYTHONPATH=.
source venv/bin/activate
python3 -m uvicorn backend.main:app --reload
```

#### Using Docker Compose:
The project uses host-mounted volumes for the database to ensure data persists directly on your machine.
```bash
docker-compose up --build
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### 4. Configuration

1. Go to the **Settings** panel in the web app.
2. Enter your **Google Client ID** and **Google Client Secret** obtained from the GCP Console (if not loaded via `.env`).
3. Click **Save Settings**.
4. Click **+ Add Account** to authenticate your Google accounts.

**Note on Permanent Deletion**: By default, the app requests the `gmail.modify` scope, which allows moving messages to the Trash but does *not* allow permanent deletion (using the "Permanently Delete" action in the Trash/Spam folders). To enable permanent deletion, you must:
1. Create a `.env` file in the project root.
2. Add `ENABLE_DELETION_SCOPE=true` to the `.env` file.
3. Restart the server and re-authenticate your accounts to grant the new `https://mail.google.com/` scope.

## Deployment

To deploy to Google Cloud Run, use the interactive script:

```bash
./gcp_setup.sh
```

Choose **Option 5** for deployment (after running Option 4 to prepare).

## Testing

Run the mock test suite:

```bash
./venv/bin/python3 -m pytest backend/tests/test_main.py
```

## Teardown

To delete the GCP project and all resources:

```bash
./gcp_setup.sh
```

Choose **Option 6**.
