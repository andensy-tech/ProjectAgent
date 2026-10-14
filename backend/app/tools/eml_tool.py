"""
EML (.eml) tool — read and write email files using Python's standard library email module.

No third-party dependencies required. Uses `email.parser` for parsing and
`email.mime` for construction.
"""

import os
import logging
import email
from email import policy
from email.parser import BytesParser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email.mime.nonmultipart import MIMENonMultipart
from email import encoders
from email.header import decode_header
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _decode_mime_header(value: str) -> str:
    """Decode a MIME encoded-header (e.g. =?UTF-8?B?...?=) to plain text."""
    if not value:
        return ""
    parts = []
    for decoded_bytes, charset in decode_header(value):
        if isinstance(decoded_bytes, bytes):
            try:
                parts.append(decoded_bytes.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                parts.append(decoded_bytes.decode("utf-8", errors="replace"))
        else:
            parts.append(str(decoded_bytes))
    return " ".join(parts)


def _decode_payload(part: Any) -> str:
    """Decode a MIME part payload to string, handling CTE."""
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def _extract_body(msg: Any) -> Tuple[str, str]:
    """Extract plain text and HTML body from an email message tree.

    Returns:
        (plain_text, html_text) — whichever is available, empty string otherwise.
    """
    plain_parts: List[str] = []
    html_parts: List[str] = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()

        if content_type == "text/plain":
            plain_parts.append(_decode_payload(part))
        elif content_type == "text/html":
            html_parts.append(_decode_payload(part))

    return "\n".join(plain_parts), "\n".join(html_parts)


def _extract_attachments(msg: Any) -> List[Dict[str, Any]]:
    """Extract attachment metadata from an email message tree."""
    attachments = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disposition.lower():
            continue

        filename = part.get_filename()
        if filename:
            filename = _decode_mime_header(filename)

        attachments.append({
            "filename": filename or "(unnamed)",
            "content_type": part.get_content_type(),
            "size": len(part.get_payload(decode=True) or b""),
        })
    return attachments


async def read_eml(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Parse an .eml file and extract email content + metadata.

    Args:
        params: {"file_path": str}
        context: Execution context

    Returns:
        {"type": "document", "tool": "read_eml", "content": str,
         "metadata": {subject, sender, recipients, date, attachments, ...}}
    """
    file_path = params.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        return {
            "type": "document", "tool": "read_eml", "content": "",
            "metadata": {}, "error": f"File not found: {file_path}",
        }

    try:
        with open(file_path, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)

        if msg is None:
            return {
                "type": "document", "tool": "read_eml", "content": "",
                "metadata": {}, "error": "Failed to parse EML file (empty or invalid)",
            }

        # Headers
        subject = _decode_mime_header(msg.get("Subject", ""))
        sender = _decode_mime_header(msg.get("From", ""))
        recipients = _decode_mime_header(msg.get("To", ""))
        cc = _decode_mime_header(msg.get("Cc", ""))
        date = msg.get("Date", "")
        message_id = msg.get("Message-ID", "")

        # Body
        plain_text, html_text = _extract_body(msg)
        content = plain_text or html_text or "(no body content)"

        # Attachments
        attachments = _extract_attachments(msg)

        metadata = {
            "file_name": os.path.basename(file_path),
            "file_size": os.path.getsize(file_path),
            "subject": subject,
            "from": sender,
            "to": recipients,
            "cc": cc,
            "date": date,
            "message_id": message_id,
            "has_html": bool(html_text),
            "attachment_count": len(attachments),
            "attachments": attachments,
        }

        return {
            "type": "document",
            "tool": "read_eml",
            "content": content,
            "metadata": metadata,
            "error": None,
        }
    except Exception as e:
        logger.error("read_eml failed: %s", e)
        return {
            "type": "document", "tool": "read_eml",
            "content": "", "metadata": {}, "error": str(e),
        }


async def write_eml(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Create an .eml file from structured email data.

    Args:
        params: {
            "file_path": str,
            "from": str,
            "to": str,
            "cc": str (optional),
            "subject": str,
            "body": str,
            "body_type": "plain" | "html" (default "plain"),
            "attachments": ["/path/to/file", ...] (optional),
        }
        context: Execution context

    Returns:
        {"type": "document", "tool": "write_eml", "content": "file_path",
         "metadata": {file_name, file_size, ...}}
    """
    file_path = params.get("file_path", "")
    if not file_path:
        return {
            "type": "document", "tool": "write_eml",
            "content": "", "metadata": {}, "error": "file_path is required",
        }

    try:
        sender = params.get("from", "")
        to = params.get("to", "")
        cc = params.get("cc", "")
        subject = params.get("subject", "")
        body = params.get("body", "")
        body_type = params.get("body_type", "plain")
        attachment_paths: List[str] = params.get("attachments", [])

        msg = MIMEMultipart()
        msg["From"] = sender
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        # Body
        subtype = "html" if body_type == "html" else "plain"
        msg.attach(MIMEText(body, subtype, "utf-8"))

        # Attachments
        attached_count = 0
        for ap in attachment_paths:
            if not os.path.exists(ap):
                logger.warning("Attachment not found, skipping: %s", ap)
                continue

            with open(ap, "rb") as af:
                attachment_data = af.read()

            part = MIMENonMultipart("application", "octet-stream")
            part.set_payload(attachment_data)
            encoders.encode_base64(part)

            filename = os.path.basename(ap)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=filename,
            )
            msg.attach(part)
            attached_count += 1

        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(msg.as_bytes())

        return {
            "type": "document",
            "tool": "write_eml",
            "content": file_path,
            "metadata": {
                "file_name": os.path.basename(file_path),
                "file_size": os.path.getsize(file_path),
                "subject": subject,
                "from": sender,
                "to": to,
                "cc": cc,
                "attachment_count": attached_count,
            },
            "error": None,
        }
    except Exception as e:
        logger.error("write_eml failed: %s", e)
        return {
            "type": "document", "tool": "write_eml",
            "content": "", "metadata": {}, "error": str(e),
        }
