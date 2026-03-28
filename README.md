# Gmail API Web App

A FastAPI-based web application that allows managing multiple Gmail accounts, viewing a unified inbox, and composing/sending emails.

## Features

- **Multi-Account Support**: Add multiple Google accounts via OAuth2.
- **Unified Inbox**: View an aggregate list of emails from all connected accounts.
- **Individual Inbox View**: View emails for a specific account.
- **Compose & Send**: Send emails from any of the connected accounts.
- **Interactive GCP Scripts**: Easy project setup, API management, and deployment.
- **Mock Tests**: Comprehensive test suite with Google API mocks.

## Prerequisites

- Python 3.9+
- Google Cloud SDK (`gcloud`)
- Docker (optional, for containerized deployment)

## Getting Started

### 1. GCP Setup

Use the interactive script to set up your Google Cloud project and enable necessary APIs:

```bash
./gcp_setup.sh
```

Choose **Option 1** to create a project and enable APIs. Follow the instructions to create OAuth2 credentials in the Google Cloud Console. Then, use **Option 2** to save your credentials to `.env`.

### 2. Local Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

### 3. Running the App

Start the FastAPI server:

```bash
cd backend
uvicorn main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

### 4. Configuration

1. Go to the **Settings** panel in the web app.
2. Enter your **Google Client ID** and **Google Client Secret** obtained from the GCP Console.
3. Click **Save Settings**.
4. Click **+ Add Account** to authenticate your Gmail accounts.

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
