# Email Classifier Application for Tensorlake
# Classifies .eml files into categories and extracts/summarizes attachments

import base64
import json
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, Field
from tensorlake.applications import (
    File,
    Image,
    application,
    function,
    run_local_application,
)

# Define custom image with dependencies
email_classifier_image = (
    Image(name="email-classifier").run("pip install openai").run("pip install supabase")
)


# Supported document types for parsing
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/msword",  # .doc
    "application/vnd.ms-excel",  # .xls
    "application/vnd.ms-powerpoint",  # .ppt
    "text/plain",
    "text/html",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/bmp",
}


class AttachmentInfo(BaseModel):
    """Information about an extracted attachment."""

    filename: str = Field(description="Original filename of the attachment")
    content_type: str = Field(description="MIME type of the attachment")
    size_bytes: int = Field(description="Size of attachment in bytes")


class AttachmentSummary(BaseModel):
    """Summary of a parsed attachment."""

    filename: str = Field(description="Original filename of the attachment")
    content_type: str = Field(description="MIME type of the attachment")
    summary: str = Field(description="AI-generated summary of the document content")
    page_count: Optional[int] = Field(
        default=None, description="Number of pages in the document"
    )
    parse_status: str = Field(
        default="success", description="Status of parsing: success, failed, unsupported"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if parsing failed"
    )
    structured_data: Optional[dict] = Field(
        default=None, description="Structured data extracted from the document"
    )
    extraction_schema: Optional[str] = Field(
        default=None, description="Name of the schema used for extraction"
    )


class EmailMetadata(BaseModel):
    """Extracted metadata from an email."""

    subject: str = Field(default="", description="Email subject line")
    sender: str = Field(default="", description="Sender email address")
    recipients: list[str] = Field(
        default_factory=list, description="List of recipient addresses"
    )
    date: Optional[str] = Field(default=None, description="Email date")
    has_attachments: bool = Field(
        default=False, description="Whether email has attachments"
    )
    body_preview: str = Field(default="", description="First 500 chars of email body")
    attachments: list[AttachmentInfo] = Field(
        default_factory=list, description="List of attachments"
    )


class EmailClassification(BaseModel):
    """Classification result for an email."""

    category: str = Field(
        description="Primary category: spam, promotional, transactional, personal, work"
    )
    confidence: float = Field(description="Confidence score between 0 and 1")
    reasoning: str = Field(description="Brief explanation of the classification")
    metadata: EmailMetadata = Field(description="Extracted email metadata")
    is_urgent: bool = Field(default=False, description="Whether email appears urgent")
    sentiment: str = Field(
        default="neutral", description="Overall sentiment: positive, negative, neutral"
    )
    attachment_summaries: list[AttachmentSummary] = Field(
        default_factory=list, description="Summaries of parsed attachments"
    )


class InvoiceItem(BaseModel):
    description: str = Field(description="Description of the item")
    amount: float = Field(description="Amount for the item")


class InvoiceData(BaseModel):
    invoice_number: str = Field(description="Invoice number")
    invoice_date: str = Field(description="Invoice date")
    total_amount: float = Field(description="Total amount due")
    due_date: str = Field(description="Payment due date")
    vendor_name: str = Field(description="Vendor company name")
    items: list[InvoiceItem] = Field(
        description="List of items or services included in the invoice"
    )


class ContractData(BaseModel):
    contract_title: str = Field(description="Title of the contract")
    parties: list[str] = Field(description="Parties involved in the contract")
    effective_date: str = Field(description="Contract effective date")
    expiration_date: str = Field(description="Contract expiration date")
    key_terms: list[str] = Field(description="Key terms and conditions")


class InsuranceData(BaseModel):
    policy_number: str = Field(description="Insurance policy number")
    policy_holder: str = Field(description="Name of the policy holder")
    provider: str = Field(description="Insurance provider/company")
    coverage_type: str = Field(
        description="Type of coverage (e.g., auto, health, home)"
    )


@application()
@function(
    image=email_classifier_image,
    description="Classifies email files (.eml) and summarizes attachments",
    memory=2.0,
    secrets=["TENSORLAKE_API_KEY", "OPENAI_API_KEY"],
)
def classify_email(eml_file: File) -> EmailClassification:
    """
    Classify an email file and extract/summarize attachments.

    This application parses .eml files and:
    1. Classifies them into categories: spam, promotional, transactional, personal, work
    2. Extracts all attachments
    3. Uses Tensorlake DocumentAI to parse the attachments
    4. Generates summaries for each attachment
    5. Does structured data extraction for attachments: invoices, contracts, insurance
    6. Uploads results to Supabase

    Args:
        eml_file: A File object containing the .eml file content

    Returns:
        EmailClassification with category, confidence, reasoning, metadata, and attachment summaries
    """
    import json
    import os

    from openai import OpenAI

    tensorlake_api_key = os.environ.get("TENSORLAKE_API_KEY")
    openai_api_key = os.environ.get("OPENAI_API_KEY")

    # Extract and decode base64-encoded content from a JSON payload
    raw_content = eml_file.content
    eml_content, filename = extract_base64_content_from_json(raw_content)

    print(f"Final eml content: {len(eml_content)} bytes, type: {type(eml_content)}")

    # Parse email content
    metadata = extract_email_metadata(eml_content)
    print(
        f"metadata extracted: Subject='{metadata.subject}', From='{metadata.sender}', To={metadata.recipients}, Has Attachments={metadata.has_attachments}"
    )

    client = OpenAI(api_key=openai_api_key)

    result = openai_classify_email(client, metadata)
    category = result["category"]
    confidence = result["confidence"]
    reasoning = result["reasoning"]

    print(f"Category: {category}, Confidence: {confidence:.2%}, Reasoning: {reasoning}")

    # Extract attachments and process them
    attachments = extract_attachments(eml_content)
    attachment_summaries = process_attachments(
        category, attachments, tensorlake_api_key, openai_api_key, metadata
    )

    email_classification_result = EmailClassification(
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        metadata=metadata,
        is_urgent=detect_urgency(metadata),
        sentiment=detect_sentiment(metadata),
        attachment_summaries=attachment_summaries,
    )

    upload_email_result_to_supabase(email_classification_result, filename)

    return email_classification_result


@function()
def extract_email_metadata(eml_content) -> EmailMetadata:
    """Parse .eml file content and extract metadata."""
    # Handle multiple input types: bytes, bytearray, string
    print(
        f"extract_email_metadata - type: {type(eml_content)}, length: {len(eml_content) if eml_content else 0}"
    )

    if isinstance(eml_content, str):
        print("Converting string to bytes")
        eml_content = eml_content.encode("utf-8")
    elif isinstance(eml_content, bytearray):
        print("Converting bytearray to bytes")
        eml_content = bytes(eml_content)
    elif eml_content is None:
        print("ERROR: eml_content is None")
        return EmailMetadata()  # Return empty metadata

    if len(eml_content) == 0:
        print("ERROR: eml_content is empty")
        return EmailMetadata()  # Return empty metadata

    try:
        msg = BytesParser(policy=policy.default).parsebytes(eml_content)
        headers = list(msg.keys())
        print(
            f"Successfully parsed message with {len(headers)} headers: {headers[:5]}..."
        )

        # Debug specific headers
        subject = msg.get("subject", "")
        sender = msg.get("from", "")
        print(f"Subject='{subject}', From='{sender}'")

    except Exception as e:
        print(f"ERROR: Failed to parse email content: {e}")
        print(
            f"ERROR: Content that failed to parse (first 100 chars): {eml_content[:100]}"
        )
        return EmailMetadata()  # Return empty metadata on parse error

    # Extract recipients with better parsing
    recipients = []
    for header_name in ["to", "cc", "bcc"]:
        header_value = msg.get(header_name)
        if header_value:
            # Handle multiple recipients separated by commas
            addrs = [addr.strip() for addr in header_value.split(",")]
            recipients.extend([addr for addr in addrs if addr])

    # Extract body with better multipart handling
    body = ""
    if msg.is_multipart():
        # Walk through all parts to find text content
        for part in msg.walk():
            # Skip the multipart container itself
            if part.is_multipart():
                continue

            content_type = part.get_content_type()
            content_disposition = part.get_content_disposition()

            # Skip attachments
            if content_disposition == "attachment":
                continue

            # Get text/plain first, then text/html as fallback
            if content_type == "text/plain" and not body:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="ignore")
                except Exception:
                    continue
            elif content_type == "text/html" and not body:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        import re

                        html_content = payload.decode("utf-8", errors="ignore")
                        # Strip HTML tags and normalize whitespace
                        body = re.sub(r"<[^>]+>", " ", html_content)
                        body = re.sub(r"\s+", " ", body).strip()
                except Exception:
                    continue
    else:
        # Handle non-multipart messages
        try:
            if msg.get_content_type() in ["text/plain", "text/html"]:
                payload = msg.get_payload(decode=True)
                if payload:
                    content = payload.decode("utf-8", errors="ignore")
                    if msg.get_content_type() == "text/html":
                        import re

                        body = re.sub(r"<[^>]+>", " ", content)
                        body = re.sub(r"\s+", " ", body).strip()
                    else:
                        body = content
        except Exception:
            # If decoding fails, try to get string payload
            payload = msg.get_payload()
            if isinstance(payload, str):
                body = payload

    # Extract attachment info with better error handling
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue

            content_disposition = part.get_content_disposition()
            if content_disposition == "attachment":
                try:
                    filename = part.get_filename() or "unnamed_attachment"
                    content_type = part.get_content_type() or "application/octet-stream"
                    payload = part.get_payload(decode=True)
                    size = len(payload) if payload else 0

                    attachments.append(
                        AttachmentInfo(
                            filename=filename,
                            content_type=content_type,
                            size_bytes=size,
                        )
                    )
                except Exception:
                    # Skip problematic attachments but don't fail
                    continue

    # Get headers with better fallback handling
    subject = msg.get("subject", "") or ""
    sender = msg.get("from", "") or ""
    date_header = msg.get("date")

    # Decode encoded headers
    try:
        from email.header import decode_header

        if subject:
            decoded = decode_header(subject)
            subject = "".join(
                [
                    (
                        part.decode(encoding or "utf-8")
                        if isinstance(part, bytes)
                        else part
                    )
                    for part, encoding in decoded
                ]
            )
        if sender:
            decoded = decode_header(sender)
            sender = "".join(
                [
                    (
                        part.decode(encoding or "utf-8")
                        if isinstance(part, bytes)
                        else part
                    )
                    for part, encoding in decoded
                ]
            )
    except Exception:
        # If header decoding fails, use as-is
        pass

    return EmailMetadata(
        subject=subject,
        sender=sender,
        recipients=recipients,
        date=date_header,
        has_attachments=len(attachments) > 0,
        body_preview=body[:500].strip() if body else "",
        attachments=attachments,
    )


@function()
def classify_with_rules(metadata: EmailMetadata) -> tuple[str, float, str]:
    """Rule-based classification as fallback or when no LLM is available."""
    subject_lower = metadata.subject.lower()
    body_lower = metadata.body_preview.lower()
    sender_lower = metadata.sender.lower()
    combined = f"{subject_lower} {body_lower} {sender_lower}"

    # Spam indicators
    spam_keywords = [
        "viagra",
        "casino",
        "lottery",
        "winner",
        "million dollars",
        "nigerian prince",
        "urgent transfer",
        "act now",
        "limited time",
        "click here",
        "unsubscribe",
        "free money",
        "congratulations you won",
    ]
    if any(kw in combined for kw in spam_keywords):
        return "spam", 0.85, "Contains common spam keywords"

    # Promotional indicators
    promo_keywords = [
        "sale",
        "discount",
        "offer",
        "deal",
        "promo",
        "coupon",
        "% off",
        "free shipping",
        "limited offer",
        "shop now",
        "newsletter",
        "subscribe",
        "marketing",
    ]
    if any(kw in combined for kw in promo_keywords):
        return "promotional", 0.80, "Contains promotional language"

    # Transactional indicators (including insurance, invoice, receipt, contract keywords)
    transactional_keywords = [
        "order confirmation",
        "receipt",
        "invoice",
        "payment",
        "shipping",
        "delivery",
        "tracking",
        "verification code",
        "password reset",
        "account",
        "subscription",
        "billing",
        # Insurance-related
        "insurance",
        "policy",
        "claim",
        "coverage",
        "deductible",
        "premium",
        "policy holder",
        "policy number",
        "claim number",
        # Invoice/Bill-related
        "invoice",
        "bill",
        "due date",
        "amount due",
        "vendor",
        # Receipt-related
        "purchase",
        "merchant",
        "total amount",
        "payment method",
        # Contract-related
        "contract",
        "agreement",
        "parties",
        "effective date",
        "terms",
    ]
    if any(kw in combined for kw in transactional_keywords):
        return "transactional", 0.82, "Contains transactional/document keywords"

    # Work indicators
    work_keywords = [
        "meeting",
        "agenda",
        "project",
        "deadline",
        "report",
        "quarterly",
        "team",
        "schedule",
        "review",
        "update",
        "stakeholder",
        "deliverable",
        "milestone",
    ]
    work_domains = ["company.com", "corp.", "inc.", "llc", "enterprise"]
    if any(kw in combined for kw in work_keywords) or any(
        d in sender_lower for d in work_domains
    ):
        return "work", 0.75, "Contains work-related keywords or domain"

    # Default to personal
    return "personal", 0.60, "No strong indicators for other categories"


@function()
def detect_urgency(metadata: EmailMetadata) -> bool:
    """Detect if email appears urgent."""
    urgent_indicators = [
        "urgent",
        "asap",
        "immediately",
        "critical",
        "important",
        "action required",
        "time sensitive",
        "deadline",
        "emergency",
    ]
    combined = f"{metadata.subject.lower()} {metadata.body_preview.lower()}"
    return any(indicator in combined for indicator in urgent_indicators)


@function()
def detect_sentiment(metadata: EmailMetadata) -> str:
    """Simple sentiment detection."""
    positive_words = [
        "thank",
        "great",
        "excellent",
        "happy",
        "pleased",
        "wonderful",
        "appreciate",
        "congratulations",
        "welcome",
        "excited",
    ]
    negative_words = [
        "sorry",
        "unfortunately",
        "problem",
        "issue",
        "complaint",
        "disappointed",
        "frustrated",
        "urgent",
        "failed",
        "error",
    ]

    combined = f"{metadata.subject.lower()} {metadata.body_preview.lower()}"

    positive_count = sum(1 for word in positive_words if word in combined)
    negative_count = sum(1 for word in negative_words if word in combined)

    if positive_count > negative_count:
        return "positive"
    elif negative_count > positive_count:
        return "negative"
    return "neutral"


@function(
    image=email_classifier_image,
    secrets=["OPENAI_API_KEY"],
)
def summarize_document_content(
    parse_result, filename: str, openai_api_key: Optional[str] = None
) -> str:
    """Generate a summary from parsed document content."""
    # Extract text from chunks or pages
    text_content = ""

    if parse_result.chunks:
        text_content = "\n".join(
            chunk.content for chunk in parse_result.chunks[:10]
        )  # First 10 chunks
    elif parse_result.pages:
        for page in parse_result.pages[:5]:  # First 5 pages
            if page.page_fragments:
                for fragment in page.page_fragments:
                    if hasattr(fragment.content, "content"):
                        text_content += fragment.content.content + "\n"

    if not text_content.strip():
        return "Document parsed but no readable text content was extracted."

    # Truncate if too long
    text_content = text_content[:4000]

    # Use OpenAI for summarization if available
    if openai_api_key:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=openai_api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": f"Summarize this document '{filename}' in 2-3 sentences:\n\n{text_content}",
                    }
                ],
                temperature=0.3,
                max_tokens=200,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            # Fall back to simple extraction
            pass

    # Simple summary: first 300 characters
    preview = text_content[:300].strip()
    if len(text_content) > 300:
        preview += "..."
    return f"Document content preview: {preview}"


@function()
def parse_email_date(date_str: Optional[str]) -> Optional[str]:
    """Parse email date string to ISO format."""
    if not date_str:
        return None

    try:
        # Try to parse common email date formats
        from email.utils import parsedate_to_datetime

        parsed_date = parsedate_to_datetime(date_str)
        return parsed_date.isoformat()
    except:
        return None


@function()
def create_extraction_schema_from_summary(summary: str, filename: str):
    """Create a StructuredExtractionOptions based on the document summary."""
    try:
        from tensorlake.documentai.models import StructuredExtractionOptions

        summary_lower = summary.lower()
        if any(
            word in summary_lower for word in ["invoice", "bill", "receipt", "purchase"]
        ):
            schema = InvoiceData.model_json_schema()
        elif any(word in summary_lower for word in ["contract", "agreement"]):
            schema = ContractData.model_json_schema()
        elif any(word in summary_lower for word in ["insurance", "policy", "claim"]):
            schema = InsuranceData.model_json_schema()
        else:
            return None

        return StructuredExtractionOptions(
            schema_name=f"Extract_{filename}", json_schema=schema, skip_ocr=False
        )
    except Exception as e:
        print(f"Failed to create extraction schema: {e}")
        return None


@function()
def process_attachments(
    category: str,
    attachments: list[tuple[str, str, bytes]],
    tensorlake_api_key: Optional[str],
    openai_api_key: Optional[str],
    metadata: EmailMetadata,
) -> list[AttachmentSummary]:
    """Process email attachments with DocumentAI and return summaries."""
    attachment_summaries = []

    if (
        (category == "transactional" or category == "work")
        and attachments
        and tensorlake_api_key
    ):
        from tensorlake.documentai import DocumentAI

        doc_ai = DocumentAI(api_key=tensorlake_api_key)

        for filename, content_type, data in attachments:
            # Check if document type is supported
            if content_type not in SUPPORTED_MIME_TYPES:
                attachment_summaries.append(
                    AttachmentSummary(
                        filename=filename,
                        content_type=content_type,
                        summary=f"Unsupported document type: {content_type}",
                        parse_status="unsupported",
                    )
                )
                continue

            try:
                # Save attachment to temp file for upload
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=Path(filename).suffix
                ) as tmp:
                    tmp.write(data)
                    tmp_path = tmp.name

                try:
                    # Upload to Tensorlake
                    file_id = doc_ai.upload(tmp_path)

                    # First, do a basic parse to get document content for analysis
                    initial_parse_result = doc_ai.parse_and_wait(file_id=file_id)

                    # Get a basic summary to understand document type
                    if openai_api_key:
                        initial_summary = summarize_document_content(
                            initial_parse_result, filename, openai_api_key
                        )

                        # Determine document type from the actual summary and try structured extraction
                        structured_extraction_options = (
                            create_extraction_schema_from_summary(
                                initial_summary, filename
                            )
                        )

                        # Use structured extraction if we have a matching schema
                        if structured_extraction_options:
                            print(
                                f"Using structured extraction for {filename} with schema {structured_extraction_options.schema_name}"
                            )
                            # Re-parse with structured extraction
                            parse_result = doc_ai.parse_and_wait(
                                file_id=file_id,
                                structured_extraction_options=[
                                    structured_extraction_options
                                ],
                            )

                            # Extract structured data
                            structured_data = (
                                parse_result.structured_data[0].data
                                if parse_result.structured_data
                                else {}
                            )
                            extraction_schema_name = (
                                structured_extraction_options.schema_name
                            )

                            # Use the initial summary as the final summary
                            summary = initial_summary
                        else:
                            # No matching schema, use initial parse and summary
                            parse_result = initial_parse_result
                            summary = initial_summary
                            structured_data = None
                            extraction_schema_name = None
                    else:
                        # No OpenAI key, use basic parsing only
                        parse_result = initial_parse_result
                        summary = f"Document parsed ({parse_result.total_pages} pages) - OPENAI_API_KEY not configured for detailed analysis"
                        structured_data = None
                        extraction_schema_name = None

                    attachment_summaries.append(
                        AttachmentSummary(
                            filename=filename,
                            content_type=content_type,
                            summary=summary,
                            page_count=parse_result.total_pages,
                            parse_status="success",
                            structured_data=structured_data,
                            extraction_schema=extraction_schema_name,
                        )
                    )
                finally:
                    # Clean up temp file
                    Path(tmp_path).unlink(missing_ok=True)

            except Exception as e:
                attachment_summaries.append(
                    AttachmentSummary(
                        filename=filename,
                        content_type=content_type,
                        summary="Failed to parse document",
                        parse_status="failed",
                        error=str(e),
                        structured_data=None,
                        extraction_schema=None,
                    )
                )
    elif attachments and not tensorlake_api_key:
        # No API key available
        for info in metadata.attachments:
            attachment_summaries.append(
                AttachmentSummary(
                    filename=info.filename,
                    content_type=info.content_type,
                    summary="TENSORLAKE_API_KEY not configured - cannot parse attachments",
                    parse_status="failed",
                    error="Missing TENSORLAKE_API_KEY",
                    structured_data=None,
                    extraction_schema=None,
                )
            )

    return attachment_summaries


@function()
def extract_attachments(eml_content: bytes) -> list[tuple[str, str, bytes]]:
    """
    Extract attachments from an email.

    Returns:
        List of tuples: (filename, content_type, data)
    """
    msg = BytesParser(policy=policy.default).parsebytes(eml_content)
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_disposition = part.get_content_disposition()
            if content_disposition == "attachment":
                filename = part.get_filename() or "unnamed_attachment"
                content_type = part.get_content_type()
                payload = part.get_payload(decode=True)
                if payload:
                    attachments.append((filename, content_type, payload))

    return attachments


@function(
    image=email_classifier_image,
    secrets=["SUPABASE_URL", "SUPABASE_KEY"],
)
def upload_email_result_to_supabase(
    classification: EmailClassification,
    filename: str = "email.eml",
    processing_duration: float = 0.0,
) -> str:
    """Upload a single email classification result to Supabase."""
    import os
    import uuid
    from datetime import datetime, timezone

    try:
        from supabase import Client, create_client

        # Get Supabase credentials
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")

        if not supabase_url or not supabase_key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY environment variables are required"
            )

        # Create Supabase client
        supabase: Client = create_client(supabase_url, supabase_key)

        # Generate unique job ID
        job_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        # Prepare job data (matching existing schema)
        job_data = {
            "total_files": 1,
            "email_files": 1,
            "emails_processed_successfully": 1,
            "emails_failed_processing": 0,
            "errors": [],
            "processing_duration_seconds": processing_duration,
            "status": "completed",
        }

        # Insert job record
        job_result = supabase.table("email_processing_jobs").insert(job_data).execute()
        job_id = job_result.data[0]["id"]
        print(f"Created job record: {job_id}")

        # Prepare email data (matching existing schema)
        email_data = {
            "job_id": job_id,
            "filename": filename,
            "file_size": 0,  # Not available for single email processing
            "category": classification.category,
            "confidence": float(classification.confidence),
            "reasoning": classification.reasoning,
            "is_urgent": classification.is_urgent,
            "sentiment": classification.sentiment,
            "subject": classification.metadata.subject,
            "sender": classification.metadata.sender,
            "recipients": classification.metadata.recipients,
            "email_date": parse_email_date(classification.metadata.date),
            "has_attachments": classification.metadata.has_attachments,
            "body_preview": classification.metadata.body_preview,
        }

        # Insert email record
        email_result = supabase.table("email_results").insert(email_data).execute()
        email_result_id = email_result.data[0]["id"]
        print(f"Created email record: {email_result_id}")

        # Insert attachment records
        for att_summary in classification.attachment_summaries:
            # Check if this attachment has structured data and route to appropriate table
            if att_summary.structured_data and att_summary.extraction_schema:
                schema_name = att_summary.extraction_schema.lower()

                if "invoice" in schema_name or "invoicedata" in schema_name:
                    # Insert into invoice_attachment_results table
                    structured_data = att_summary.structured_data
                    invoice_data = {
                        "email_result_id": email_result_id,
                        "filename": att_summary.filename,
                        "content_type": att_summary.content_type,
                        "page_count": att_summary.page_count,
                        "parse_status": att_summary.parse_status,
                        "summary": att_summary.summary,
                        "error_message": att_summary.error,
                        "extraction_schema": att_summary.extraction_schema,
                        # Invoice-specific fields
                        "invoice_number": structured_data.get("invoice_number"),
                        "total_amount": structured_data.get("total_amount"),
                        "due_date": structured_data.get("due_date"),
                        "vendor_name": structured_data.get("vendor_name"),
                        "invoice_date": structured_data.get("invoice_date"),
                        "items": structured_data.get("items"),  # JSON array
                    }

                    supabase.table("invoice_attachment_results").insert(
                        invoice_data
                    ).execute()
                    print(
                        f"Created invoice attachment record for: {att_summary.filename}"
                    )

                elif "contract" in schema_name or "contractdata" in schema_name:
                    # Insert into contract_attachment_results table
                    structured_data = att_summary.structured_data
                    contract_data = {
                        "email_result_id": email_result_id,
                        "filename": att_summary.filename,
                        "content_type": att_summary.content_type,
                        "page_count": att_summary.page_count,
                        "parse_status": att_summary.parse_status,
                        "summary": att_summary.summary,
                        "error_message": att_summary.error,
                        "extraction_schema": att_summary.extraction_schema,
                        # Contract-specific fields
                        "contract_title": structured_data.get("contract_title"),
                        "parties": structured_data.get("parties"),  # JSON array
                        "effective_date": structured_data.get("effective_date"),
                        "expiration_date": structured_data.get("expiration_date"),
                        "key_terms": structured_data.get("key_terms"),  # JSON array
                    }

                    supabase.table("contract_attachment_results").insert(
                        contract_data
                    ).execute()
                    print(
                        f"Created contract attachment record for: {att_summary.filename}"
                    )

                elif "insurance" in schema_name or "insurancedata" in schema_name:
                    # Insert into insurance_attachment_results table
                    structured_data = att_summary.structured_data
                    insurance_data = {
                        "email_result_id": email_result_id,
                        "filename": att_summary.filename,
                        "content_type": att_summary.content_type,
                        "page_count": att_summary.page_count,
                        "parse_status": att_summary.parse_status,
                        "summary": att_summary.summary,
                        "error_message": att_summary.error,
                        "extraction_schema": att_summary.extraction_schema,
                        # Insurance-specific fields
                        "policy_number": structured_data.get("policy_number"),
                        "policy_holder": structured_data.get("policy_holder"),
                        "provider": structured_data.get("provider"),
                        "coverage_type": structured_data.get("coverage_type"),
                        "effective_date": structured_data.get("effective_date"),
                        "expiration_date": structured_data.get("expiration_date"),
                        "claim_number": structured_data.get("claim_number"),
                        "claim_amount": structured_data.get("claim_amount"),
                        "status": structured_data.get("status"),
                    }

                    supabase.table("insurance_attachment_results").insert(
                        insurance_data
                    ).execute()
                    print(
                        f"Created insurance attachment record for: {att_summary.filename}"
                    )

                else:
                    # Unknown schema, fall back to generic attachment_results
                    attachment_data = {
                        "email_result_id": email_result_id,
                        "filename": att_summary.filename,
                        "content_type": att_summary.content_type,
                        "page_count": att_summary.page_count,
                        "parse_status": att_summary.parse_status,
                        "summary": att_summary.summary,
                        "error_message": att_summary.error,
                        "structured_data": att_summary.structured_data,
                        "extraction_schema": att_summary.extraction_schema,
                    }

                    supabase.table("attachment_results").insert(
                        attachment_data
                    ).execute()
                    print(
                        f"Created generic attachment record for: {att_summary.filename}"
                    )

            else:
                # No structured data, use generic attachment_results table
                attachment_data = {
                    "email_result_id": email_result_id,
                    "filename": att_summary.filename,
                    "content_type": att_summary.content_type,
                    "page_count": att_summary.page_count,
                    "parse_status": att_summary.parse_status,
                    "summary": att_summary.summary,
                    "error_message": att_summary.error,
                    "structured_data": att_summary.structured_data,
                    "extraction_schema": att_summary.extraction_schema,
                }

                supabase.table("attachment_results").insert(attachment_data).execute()
                print(f"Created generic attachment record for: {att_summary.filename}")

        return job_id

    except Exception as e:
        print(f"Failed to upload to Supabase: {e}")
        raise e


def extract_base64_content_from_json(
    raw_content: bytes,
) -> Tuple[bytes, Optional[str]]:
    """
    Extracts and decodes base64-encoded content from a JSON payload.

    Expected JSON shape:
    {
        "content": "<base64 string>",
        "filename": "optional filename"
    }

    Returns:
        (decoded_content_bytes, filename)

    Raises:
        ValueError: if JSON is invalid or content is missing
    """
    print("Detected direct content JSON request...")

    try:
        content_str = raw_content.decode("utf-8")
        json_data = json.loads(content_str)
    except Exception as e:
        raise ValueError("Failed to decode or parse JSON content") from e

    base64_content = json_data.get("content")
    if not base64_content:
        raise ValueError("No content found in JSON")

    try:
        decoded_content = base64.b64decode(base64_content)
    except Exception as e:
        raise ValueError("Failed to decode base64 content") from e

    filename = json_data.get("filename") or "email.eml"

    print(f"Extracted and decoded direct content: {len(decoded_content)} bytes")

    return decoded_content, filename


def openai_classify_email(
    client,
    metadata,
    *,
    model: str = "gpt-4o-mini",
    temperature: float = 0.1,
    max_tokens: int = 200,
) -> Dict[str, Any]:
    """
    Classify an email into a single category using OpenAI.

    Returns:
        {
            "category": str,
            "confidence": float,
            "reasoning": str
        }

    Raises:
        ValueError if the model response is invalid JSON or missing fields
    """
    prompt = f"""Classify this email into exactly one category: spam, promotional, transactional, personal, or work.

Email Details:
- Subject: {metadata.subject}
- From: {metadata.sender}
- To: {', '.join(metadata.recipients[:3])}
- Has Attachments: {metadata.has_attachments}
- Body Preview: {metadata.body_preview}

Respond with ONLY a JSON object in this exact format:
{{"category": "<category>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}}"""

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )

    raw_content = response.choices[0].message.content

    try:
        result = json.loads(raw_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON returned by model: {raw_content}") from e

    for field in ("category", "confidence", "reasoning"):
        if field not in result:
            raise ValueError(f"Missing field '{field}' in model response: {result}")

    return result


if __name__ == "__main__":
    import sys

    # Example usage with a sample .eml file
    if len(sys.argv) > 1:
        eml_path = sys.argv[1]
        with open(eml_path, "rb") as f:
            eml_content = f.read()

        eml_file = File(content=eml_content, content_type="message/rfc822")
        request = run_local_application(classify_email, eml_file)
        result = request.output()

        print(f"\nEmail Classification Results:")
        print(f"  Category: {result.category}")
        print(f"  Confidence: {result.confidence:.2%}")
        print(f"  Reasoning: {result.reasoning}")
        print(f"  Is Urgent: {result.is_urgent}")
        print(f"  Sentiment: {result.sentiment}")
        print(f"\nMetadata:")
        print(f"  Subject: {result.metadata.subject}")
        print(f"  From: {result.metadata.sender}")
        print(f"  To: {', '.join(result.metadata.recipients[:3])}")
        print(f"  Has Attachments: {result.metadata.has_attachments}")

        if result.attachment_summaries:
            print(f"\nAttachment Summaries ({len(result.attachment_summaries)}):")
            for i, att in enumerate(result.attachment_summaries, 1):
                print(f"\n  [{i}] {att.filename} ({att.content_type})")
                print(f"      Status: {att.parse_status}")
                if att.page_count:
                    print(f"      Pages: {att.page_count}")
                print(f"      Summary: {att.summary}")
                if att.error:
                    print(f"      Error: {att.error}")
    else:
        # Demo with sample email content
        sample_eml = b"""From: newsletter@shop.example.com
To: user@example.com
Subject: 50% OFF Everything - Limited Time Sale!
Date: Mon, 9 Dec 2024 10:00:00 -0500
Content-Type: text/plain; charset="utf-8"
Message-ID: <12345@shop.example.com>

Hi there!

Don't miss our biggest sale of the year! Get 50% off everything in store.

Shop now at example.com/sale - use code SAVE50 at checkout.

Best regards,
The Shop Team

Unsubscribe: example.com/unsubscribe
"""

        eml_file = File(content=sample_eml, content_type="message/rfc822")
        request = run_local_application(classify_email, eml_file)
        result = request.output()

        print(f"\nDemo Email Classification:")
        print(f"  Category: {result.category}")
        print(f"  Confidence: {result.confidence:.2%}")
        print(f"  Reasoning: {result.reasoning}")
        print(f"  Sentiment: {result.sentiment}")
        print(f"  Sentiment: {result.sentiment}")
