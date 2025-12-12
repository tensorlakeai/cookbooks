"""
This is the sample code to use the barcode detection feature of Tensorlake Docuemnt AI
Checkout the documentation: https://docs.tensorlake.ai/document-ingestion/parsing/read#parsing-options
"""

import os

from dotenv import load_dotenv
from tensorlake.documentai import (
    DocumentAI,
    ParseStatus,
    ParsingOptions,
)

load_dotenv()
tensorlake_api_token = os.getenv("TENSORLAKE_API_TOKEN")


doc_ai = DocumentAI(api_key=tensorlake_api_token)
file_id = doc_ai.upload(path="/Users/tunejedi/Downloads/ben_20240918_008.pdf")

parsing_options = ParsingOptions(
    ocr_model="model03",  # type: ignore
    barcode_detection="true",  # type: ignore
)

parse_id = doc_ai.read(
    file_id=file_id,
    page_range="1-3",
    parsing_options=parsing_options,
)

result = doc_ai.wait_for_completion(parse_id)

if result.status == ParseStatus.SUCCESSFUL:
    for chunk in result.chunks:  # type: ignore
        print(chunk.content)
