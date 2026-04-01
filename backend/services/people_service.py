import logging
from datetime import datetime
from googleapiclient.discovery import build
from sqlmodel import Session, select
from backend.db import engine
from backend.models import Account, GoogleContact
from backend.services.gmail_service import get_google_credentials

logger = logging.getLogger(__name__)

def get_people_service(account_id: int, session: Session):
    creds = get_google_credentials(account_id, session)
    if not creds:
        return None
    return build("people", "v1", credentials=creds)

async def sync_google_contacts(account_id: int):
    # Sync both connections and other contacts
    with Session(engine) as session:
        account = session.get(Account, account_id)
        if not account: return
        
        service = get_people_service(account_id, session)
        if not service: return

        # 1. Sync Connections
        try:
            sync_token = account.sync_token
            next_page_token = None
            while True:
                kwargs = {
                    "resourceName": "people/me",
                    "personFields": "names,emailAddresses,metadata,photos,memberships",
                    "pageSize": 1000,
                    "requestSyncToken": True
                }
                if sync_token:
                    kwargs["syncToken"] = sync_token
                if next_page_token:
                    kwargs["pageToken"] = next_page_token
                
                results = service.people().connections().list(**kwargs).execute()
                connections = results.get("connections", [])
                
                for person in connections:
                    res_name = person.get("resourceName")
                    metadata = person.get("metadata", {})
                    if metadata.get("deleted"):
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                    else:
                        name = person.get("names", [{}])[0].get("displayName") if person.get("names") else None
                        photo_url = person.get("photos", [{}])[0].get("url") if person.get("photos") else None
                        is_starred = False
                        if person.get("memberships"):
                            for m in person["memberships"]:
                                if m.get("contactGroupMembership", {}).get("contactGroupResourceName") == "contactGroups/starred":
                                    is_starred = True
                                    break
                        
                        emails = person.get("emailAddresses", [])
                        if not emails: continue
                        
                        # Delete old entries for this resource
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                        
                        for e in emails:
                            email_addr = e.get("value")
                            if email_addr:
                                new_contact = GoogleContact(
                                    account_id=account_id,
                                    resource_name=res_name,
                                    email=email_addr,
                                    name=name,
                                    photo_url=photo_url,
                                    is_starred=is_starred
                                )
                                session.add(new_contact)
                
                next_sync_token = results.get("nextSyncToken")
                next_page_token = results.get("nextPageToken")
                
                if not next_page_token:
                    account.sync_token = next_sync_token
                    session.add(account)
                    session.commit()
                    break
        except Exception as e:
            logger.error(f"Error syncing connections for {account_id}: {e}")
            if "expired" in str(e).lower():
                account.sync_token = None
                session.add(account)
                session.commit()

        # 2. Sync Other Contacts
        try:
            other_sync_token = account.other_sync_token
            next_page_token = None
            while True:
                kwargs = {
                    "pageSize": 1000,
                    "readMask": "names,emailAddresses,metadata,photos",
                    "requestSyncToken": True
                }
                if other_sync_token:
                    kwargs["syncToken"] = other_sync_token
                if next_page_token:
                    kwargs["pageToken"] = next_page_token
                
                results = service.otherContacts().list(**kwargs).execute()
                other_contacts = results.get("otherContacts", [])
                
                for person in other_contacts:
                    res_name = person.get("resourceName")
                    metadata = person.get("metadata", {})
                    if metadata.get("deleted"):
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                    else:
                        name = person.get("names", [{}])[0].get("displayName") if person.get("names") else None
                        photo_url = person.get("photos", [{}])[0].get("url") if person.get("photos") else None
                        
                        emails = person.get("emailAddresses", [])
                        if not emails: continue
                        
                        # Delete old entries for this resource
                        existing = session.exec(
                            select(GoogleContact)
                            .where(GoogleContact.account_id == account_id)
                            .where(GoogleContact.resource_name == res_name)
                        ).all()
                        for c in existing: session.delete(c)
                        
                        for e in emails:
                            email_addr = e.get("value")
                            if email_addr:
                                new_contact = GoogleContact(
                                    account_id=account_id,
                                    resource_name=res_name,
                                    email=email_addr,
                                    name=name,
                                    photo_url=photo_url,
                                    is_starred=False
                                )
                                session.add(new_contact)
                
                next_sync_token = results.get("nextSyncToken")
                next_page_token = results.get("nextPageToken")
                
                if not next_page_token:
                    account.other_sync_token = next_sync_token
                    account.last_contact_sync = datetime.utcnow()
                    session.add(account)
                    session.commit()
                    break
        except Exception as e:
            logger.error(f"Error syncing other contacts for {account_id}: {e}")
            if "expired" in str(e).lower():
                account.other_sync_token = None
                session.add(account)
                session.commit()
