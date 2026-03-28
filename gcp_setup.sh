#!/bin/bash

# ANSI Color Codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=======================================${NC}"
echo -e "${BLUE}   Gmail API App GCP Management Tool   ${NC}"
echo -e "${BLUE}=======================================${NC}"

function show_menu() {
    echo -e "\n${YELLOW}Please choose an action:${NC}"
    echo "1) Create GCP Project & Enable APIs"
    echo "2) Enable/Disable Gmail API only"
    echo "3) Deploy Stack to Cloud Run"
    echo "4) Teardown (Delete Project)"
    echo "q) Quit"
    read -p "Action: " choice
}

function check_gcloud() {
    if ! command -v gcloud &> /dev/null; then
        echo -e "${RED}Error: gcloud CLI is not installed.${NC}"
        exit 1
    fi
}

function create_project() {
    read -p "Enter a unique Project ID: " PROJECT_ID
    echo -e "${GREEN}Creating project $PROJECT_ID...${NC}"
    gcloud projects create "$PROJECT_ID"
    gcloud config set project "$PROJECT_ID"
    
    echo -e "${GREEN}Enabling APIs (Gmail, Cloud Run, Artifact Registry)...${NC}"
    gcloud services enable gmail.googleapis.com run.googleapis.com artifactregistry.googleapis.com
    
    echo -e "${YELLOW}Important:${NC} You MUST create OAuth2 credentials manually in the console:"
    echo "https://console.cloud.google.com/apis/credentials?project=$PROJECT_ID"
    echo "1. Create 'OAuth client ID' for 'Web application'."
    echo "2. Add Authorized Redirect URI: http://localhost:8000/auth/callback (or your Cloud Run URL later)"
}

function toggle_gmail_api() {
    read -p "Enable or Disable? (e/d): " action
    if [ "$action" == "e" ]; then
        gcloud services enable gmail.googleapis.com
        echo -e "${GREEN}Gmail API enabled.${NC}"
    else
        gcloud services disable gmail.googleapis.com
        echo -e "${RED}Gmail API disabled.${NC}"
    fi
}

function deploy_stack() {
    read -p "Enter Service Name (default: gmail-app): " SERVICE_NAME
    SERVICE_NAME=${SERVICE_NAME:-gmail-app}
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
        2) toggle_gmail_api ;;
        3) deploy_stack ;;
        4) teardown ;;
        q) exit 0 ;;
        *) echo -e "${RED}Invalid choice${NC}" ;;
    esac
done
