"""Cognito pre-signup trigger for podbay mobile: only the addresses in
ALLOWED_EMAILS get an account, and a Google sign-in with one of them is
linked to the existing native user, so there is one user and one sub
(jeeves practices/auth.md). The first Google sign-in after the link
fails once by design; the second one lands in the native user.
"""

from __future__ import annotations

import os

import boto3

ALLOWED = {e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()}


def handler(event: dict, context=None) -> dict:
    email = str((event.get("request") or {}).get("userAttributes", {}).get("email") or "").lower()
    if email not in ALLOWED:
        raise Exception("This app has one user.")  # Cognito shows the sign-in as refused
    if event.get("triggerSource") == "PreSignUp_ExternalProvider":
        pool = event["userPoolId"]
        provider, _, provider_id = event["userName"].partition("_")
        cognito = boto3.client("cognito-idp")
        users = cognito.list_users(UserPoolId=pool, Filter=f'email = "{email}"').get("Users", [])
        native = next((u for u in users if u.get("UserStatus") != "EXTERNAL_PROVIDER"), None)
        if native:
            cognito.admin_link_provider_for_user(
                UserPoolId=pool,
                DestinationUser={"ProviderName": "Cognito", "ProviderAttributeValue": native["Username"]},
                SourceUser={"ProviderName": provider.capitalize() if provider.lower() == "google" else provider,
                            "ProviderAttributeName": "Cognito_Subject", "ProviderAttributeValue": provider_id},
            )
            raise Exception("Linked your Google sign-in. Sign in again.")
    if event.get("triggerSource") != "PreSignUp_AdminCreateUser":
        event.setdefault("response", {})
        event["response"]["autoConfirmUser"] = True
        event["response"]["autoVerifyEmail"] = True
    return event
