#!/bin/bash

# ANSI Color Codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=======================================${NC}"
echo -e "${BLUE}   Prosciutto GCP Management Tool   ${NC}"
echo -e "${BLUE}=======================================${NC}"

function show_menu() {
    echo -e "\n${YELLOW}Please choose an action:${NC}"
    echo "1) Create GCP Project & Enable Gmail/People APIs (Local Dev)"
    echo "2) Configure Local OAuth Credentials (.env)"
    echo "3) Enable/Disable Gmail and People APIs"
    echo "4) Prepare for Cloud Run (Requires Billing)"
    echo "5) Deploy Stack to Cloud Run"
    echo "6) Teardown (Delete Project)"
    echo "q) Quit"
    read -p "Action: " choice
}

function check_gcloud() {
    if ! command -v gcloud &> /dev/null; then
        echo -e "${RED}Error: gcloud CLI is not installed.${NC}"
        exit 1
    fi
}

function configure_local_env() {
    echo -e "\n${BLUE}--- Local OAuth Configuration ---${NC}"
    echo "Choose application type:"
    echo "1) Web application (Client ID + Secret) - Recommended for most uses."
    echo "2) Desktop app (Client ID only) - Use for local single-user docker/dev only."
    read -p "Type (1/2, default 1): " APP_TYPE_CHOICE
    
    if [ "$APP_TYPE_CHOICE" == "2" ]; then
        OAUTH_APP_TYPE="desktop"
    else
        OAUTH_APP_TYPE="web"
    fi

    read -p "Enter Google Client ID: " GOOGLE_CLIENT_ID
    if [ "$OAUTH_APP_TYPE" == "web" ]; then
        read -p "Enter Google Client Secret: " GOOGLE_CLIENT_SECRET
    else
        GOOGLE_CLIENT_SECRET=""
    fi
    
    # Use current project ID if available
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
    read -p "Enter Google Project ID (default: $PROJECT_ID): " ENTERED_PROJECT_ID
    PROJECT_ID=${ENTERED_PROJECT_ID:-$PROJECT_ID}
    
    ENV_FILE="backend/.env"
    mkdir -p "backend"
    
    echo -e "${GREEN}Writing configuration to $ENV_FILE...${NC}"
    echo "OAUTH_APP_TYPE=$OAUTH_APP_TYPE" > "$ENV_FILE"
    echo "GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID" >> "$ENV_FILE"
    echo "GOOGLE_CLIENT_SECRET=$GOOGLE_CLIENT_SECRET" >> "$ENV_FILE"
    echo "GOOGLE_PROJECT_ID=$PROJECT_ID" >> "$ENV_FILE"
    
    echo -e "${GREEN}Successfully configured $ENV_FILE.${NC}"
}

function create_project() {
    read -p "Enter a unique Project ID: " PROJECT_ID
    echo -e "${GREEN}Creating project $PROJECT_ID...${NC}"
    gcloud projects create "$PROJECT_ID"
    gcloud config set project "$PROJECT_ID"
    
    echo -e "${GREEN}Enabling Gmail and People APIs...${NC}"
    gcloud services enable gmail.googleapis.com people.googleapis.com
    
    echo -e "${YELLOW}Important:${NC} You MUST create OAuth2 credentials manually in the console:"
    echo "https://console.cloud.google.com/apis/credentials?project=$PROJECT_ID"
    echo "1. Click 'Create Credentials' -> 'OAuth client ID'."
    echo "2. Select 'Web application' (standard) OR 'Desktop app' (local single-user only)."
    echo "3. For BOTH types, use redirect URI: http://localhost:8000/auth/callback"

    read -p "Do you want to configure your local .env file now? (y/n): " configure_now
    if [ "$configure_now" == "y" ]; then
        configure_local_env
    fi
}

function prepare_cloud_run() {
    echo -e "${YELLOW}To enable Cloud Run and Artifact Registry, you need to link a Billing Account.${NC}"
    
    PROJECT_ID=$(gcloud config get-value project)
    if [ -z "$PROJECT_ID" ]; then
        read -p "Enter your Project ID: " PROJECT_ID
        gcloud config set project "$PROJECT_ID"
    fi

    echo "Here are your available billing accounts:"
    gcloud billing accounts list
    read -p "Enter the Billing Account ID to link (or press enter to skip): " BILLING_ID
    
    if [ -n "$BILLING_ID" ]; then
        echo -e "${GREEN}Linking billing account $BILLING_ID to project $PROJECT_ID...${NC}"
        gcloud billing projects link "$PROJECT_ID" --billing-account "$BILLING_ID"
    fi
    
    echo -e "${GREEN}Enabling APIs (Cloud Run, Artifact Registry)...${NC}"
    gcloud services enable run.googleapis.com artifactregistry.googleapis.com
    
    echo -e "${YELLOW}Note:${NC} Ensure your OAuth credentials have your Cloud Run URL added as an Authorized Redirect URI."
}

function toggle_email_contact_apis() {
    read -p "Enable or Disable Gmail and People APIs? (e/d): " action
    if [ "$action" == "e" ]; then
        gcloud services enable gmail.googleapis.com people.googleapis.com
        echo -e "${GREEN}Gmail and People APIs enabled.${NC}"
    else
        gcloud services disable gmail.googleapis.com people.googleapis.com
        echo -e "${RED}Gmail and People APIs disabled.${NC}"
    fi
}

function deploy_stack() {
    # Warn if using desktop mode
    if grep -q "OAUTH_APP_TYPE=desktop" backend/.env 2>/dev/null; then
        echo -e "${YELLOW}Warning: Current configuration is 'desktop' mode.${NC}"
        echo -e "${YELLOW}Cloud Run deployments typically require 'web' application type.${NC}"
        read -p "Continue anyway? (y/n): " confirm_deploy
        if [ "$confirm_deploy" != "y" ]; then return; fi
    fi

    read -p "Enter Service Name (default: prosciutto): " SERVICE_NAME
    SERVICE_NAME=${SERVICE_NAME:-prosciutto}
    read -p "Enter Region (default: us-central1): " REGION
    REGION=${REGION:-us-central1}
    
    echo -e "${GREEN}Deploying to Cloud Run...${NC}"
    gcloud run deploy "$SERVICE_NAME" --source . --region "$REGION" --allow-unauthenticated
}

function teardown() {
    read -p "Enter Project ID to delete: " PROJECT_ID
    echo -e "${RED}WARNING: This will delete the project and all resources!${NC}"
    read -p "Are you sure? (y/n): " confirm
    if [ "$confirm" == "y" ]; then
        gcloud projects delete "$PROJECT_ID"
        echo -e "${GREEN}Project $PROJECT_ID deleted.${NC}"
    fi
}

check_gcloud

while true; do
    show_menu
    case $choice in
        1) create_project ;;
        2) configure_local_env ;;
        3) toggle_email_contact_apis ;;
        4) prepare_cloud_run ;;
        5) deploy_stack ;;
        6) teardown ;;
        q) exit 0 ;;
        *) echo -e "${RED}Invalid choice${NC}" ;;
    esac
done
