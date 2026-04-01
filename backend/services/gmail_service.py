import json
import logging
import base64
from datetime import datetime, timedelta
from typing import Optional, List
from email.utils import getaddresses

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlmodel import Session, select, delete
from sqlalchemy import desc

from backend.db import engine
from backend.models import Account, RecentContact
from backend.core.config import SCOPES

logger = logging.getLogger(__name__)

def get_google_credentials(account_id: int, session: Session):
    account = session.get(Account, account_id)
    if not account:
        logger.error(f"Account {account_id} not found in database")
        return None

    try:
        creds_dict = json.loads(account.credentials_json)
        creds = Credentials.from_authorized_user_info(creds_dict, SCOPES)

        # Check if creds need refresh
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request as GoogleRequest
            creds.refresh(GoogleRequest())
            # Update DB with new credentials
            account.credentials_json = creds.to_json()
            session.add(account)
            session.commit()

        return creds
    except Exception as e:
        logger.error(f"Failed to get Google credentials for account {account_id}: {str(e)}")
        return None

def get_gmail_service(account_id: int, session: Session):
    creds = get_google_credentials(account_id, session)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds)

def get_header(headers, name):
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return None

def get_detailed_messages_batch(service, messages_meta, format="metadata", metadata_headers=None):
    if not messages_meta:
        return []

    detailed_messages_dict = {}

    def callback(request_id, response, exception):
        if exception is not None:
            return
        
        headers = response.get("payload", {}).get("headers", [])
        msg_data = {
            "id": response["id"],
            "snippet": response.get("snippet", ""),
            "threadId": response.get("threadId", ""),
            "labelIds": response.get("labelIds") or [],
            "internalDate": int(response.get("internalDate", 0)),
        }
        
        if metadata_headers:
            for header_name in metadata_headers:
                msg_data[header_name] = get_header(headers, header_name)
                msg_data[header_name.lower()] = msg_data[header_name]
        else:
            msg_data["subject"] = get_header(headers, "Subject")
            msg_data["from"] = get_header(headers, "From")
            msg_data["date"] = get_header(headers, "Date")
            msg_data["Subject"] = msg_data["subject"]
            msg_data["From"] = msg_data["from"]
            msg_data["Date"] = msg_data["date"]
            
        detailed_messages_dict[request_id] = msg_data

    batch = service.new_batch_http_request(callback=callback)
    
    for msg in messages_meta:
        kwargs = {"userId": "me", "id": msg["id"], "format": format}
        if metadata_headers:
            kwargs["metadataHeaders"] = metadata_headers
        batch.add(service.users().messages().get(**kwargs), request_id=msg["id"])
    
    batch.execute()

    results = []
    for msg in messages_meta:
        if msg["id"] in detailed_messages_dict:
            results.append(detailed_messages_dict[msg["id"]])
    return results

def extract_contacts(address_str: str) -> List[dict]:
    if not address_str:
        return []
    contacts = []
    for name, email_addr in getaddresses([address_str]):
        if email_addr:
            contacts.append({"name": name, "email": email_addr})
    return contacts

async def update_recent_contact(account_id: int, email: str, name: Optional[str], session: Session):
    existing = session.exec(
        select(RecentContact)
        .where(RecentContact.account_id == account_id)
        .where(RecentContact.email == email)
    ).first()
    
    if existing:
        existing.last_interacted = datetime.utcnow()
        if name and not existing.name:
            existing.name = name
        session.add(existing)
    else:
        new_recent = RecentContact(
            account_id=account_id,
            email=email,
            name=name,
            last_interacted=datetime.utcnow()
        )
        session.add(new_recent)
    
    session.commit()
    
    ninety_days_ago = datetime.utcnow() - timedelta(days=90)
    session.exec(
        delete(RecentContact)
        .where(RecentContact.account_id == account_id)
        .where(RecentContact.last_interacted < ninety_days_ago)
    )
    session.commit()
    
    recents = session.exec(
        select(RecentContact)
        .where(RecentContact.account_id == account_id)
        .order_by(desc(RecentContact.last_interacted))
    ).all()
    
    if len(recents) > 100:
        for extra in recents[100:]:
            session.delete(extra)
        session.commit()

async def sync_recent_contacts_warmup(account_id: int):
    with Session(engine) as session:
        service = get_gmail_service(account_id, session)
        if not service:
            return
        
        thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).strftime("%Y/%m/%d")
        query = f"in:sent after:{thirty_days_ago}"
        
        try:
            results = service.users().messages().list(userId="me", q=query, maxResults=500).execute()
            messages_meta = results.get("messages", [])
            
            if not messages_meta:
                return

            detailed_messages = get_detailed_messages_batch(
                service, 
                messages_meta, 
                format="metadata", 
                metadata_headers=["To", "Cc", "Bcc"]
            )
            
            for msg in detailed_messages:
                msg_date = datetime.fromtimestamp(msg["internalDate"] / 1000.0)
                
                for header in ["to", "cc", "bcc"]:
                    val = msg.get(header)
                    if val:
                        for contact in extract_contacts(val):
                            existing = session.exec(
                                select(RecentContact)
                                .where(RecentContact.account_id == account_id)
                                .where(RecentContact.email == contact["email"])
                            ).first()
                            
                            if existing:
                                if msg_date > existing.last_interacted:
                                    existing.last_interacted = msg_date
                                if contact["name"] and not existing.name:
                                    existing.name = contact["name"]
                                session.add(existing)
                            else:
                                new_recent = RecentContact(
                                    account_id=account_id,
                                    email=contact["email"],
                                    name=contact["name"],
                                    last_interacted=msg_date
                                )
                                session.add(new_recent)
            
            session.commit()
            
            ninety_days_ago = datetime.utcnow() - timedelta(days=90)
            session.exec(
                delete(RecentContact)
                .where(RecentContact.account_id == account_id)
                .where(RecentContact.last_interacted < ninety_days_ago)
            )
            session.commit()
            
            recents = session.exec(
                select(RecentContact)
                .where(RecentContact.account_id == account_id)
                .order_by(desc(RecentContact.last_interacted))
            ).all()
            
            if len(recents) > 100:
                for extra in recents[100:]:
                    session.delete(extra)
                session.commit()
                
        except Exception as e:
            logger.error(f"Error during recent contacts warm-up for account {account_id}: {e}")
