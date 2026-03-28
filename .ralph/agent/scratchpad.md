# Gmail API Web App Development

## Objective
Create a Gmail API web app with FastAPI that meets the following criteria:
- Deployable as a docker container
- Persistent settings and configured accounts
- Support for multiple configured accounts
- Mock framework for testing Gmail API calls
- Support for reading and displaying mailboxes
- Respectful caching and rate-limiting for respectful use of the Gmail API
- Frontend showing mailboxes in a left column, message list of the selected mailbox to the right

## Architecture & Tech Stack
- **Backend**: FastAPI (Python)
- **Database**: SQLite (SQLAlchemy or SQLModel) for account and setting persistence
- **Gmail API**: Google API Python Client
- **Authentication**: Google OAuth2 (will need a way to manage multiple account credentials)
- **Frontend**: React (Vite) + Tailwind CSS
- **Caching**: Redis (in Docker) or simple in-memory cache/SQLite cache for mailboxes
- **Mocking**: Mocked Gmail API client for development/testing

## Plan
1.  **Project Initialization**: Setup directory structure, FastAPI boilerplate, and Dockerfile/docker-compose.
2.  **Database & Multi-Account Support**: Implement the data model for accounts and settings.
3.  **OAuth2 Implementation**: Handle Google OAuth2 flow for multiple accounts.
4.  **Gmail API Integration**: Client wrapper for Gmail API with rate-limiting and caching.
5.  **Mock Framework**: Create a mock Gmail API client for testing without real credentials.
6.  **Backend API Endpoints**:
    - List accounts
    - List mailboxes (labels) for an account
    - List messages in a mailbox
7.  **Frontend Implementation**:
    - Account management UI
    - Two-column layout: Labels on the left, messages on the right
8.  **Final Polish & Testing**: Docker configuration, documentation, and final verification.
