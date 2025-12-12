# Using Outlines with Tensorlake: Schema-Enforced Invoice Extraction

This sample shows how to combine **Tensorlake Document AI** with **Outlines** and **OpenAI** to build a **schema-enforced** invoice extraction pipeline.

It follows the [**Building Clean, Schema-Enforced Pipelines with Tensorlake + Outlines**](https://www.tensorlake.ai/blog/outlines) blog post.

---

## Project Description

In this project, you will:

1. Upload and parse an `invoice.pdf` with **Tensorlake** using the `DocumentAI` API.
2. Combine the parsed fragments into a `document_text` string.
3. Define a Pydantic `Invoice` model that describes the fields you care about
   (invoice number, dates, vendor, total amount, etc.).
4. Use **Outlines** to create a prompt template with a few invoice examples.
5. Call an OpenAI model through Outlines to generate JSON that **must** match
   the `Invoice` schema.

The result is a clean, reliable JSON representation of the invoice that’s ready
for databases, ETL pipelines, or downstream automations.

---

## Requirements

- Python **3.9+**
- A **Tensorlake** account and API key
- An **OpenAI** account and API key
- `pip` (or another Python package manager)
- Jupyter (Notebook or Lab) to run the `.ipynb`

---

## Installation

From the repository root:

```bash
cd using-outline-with-tensorlake
````

Install the dependencies:

```bash
pip install tensorlake outlines openai pydantic jupyter
```

> Optionally, you can create a virtual environment first:

```bash
uv venv .venv
source .venv/bin/activate
```

---

## Environment Variables

The notebook expects the following environment variables to be set:

```bash
export TENSORLAKE_API_KEY="your_tensorlake_api_key"
export OPENAI_API_KEY="your_openai_api_key"
```

You can also choose to load them from a `.env` file or a secret manager if you prefer.

---

## Running the Notebook

From the repository root:

```bash
cd using-outline-with-tensorlake
jupyter lab
# or
jupyter notebook
```

Then:

1. Open **`Building Clean Schema-Enforced Pipelines with Tensorlake.ipynb`**.
2. Run the cells in order:

   * Install/import dependencies (if needed).
   * Configure API keys.
   * Parse `invoice.pdf` with Tensorlake.
   * Build the prompt and call Outlines + OpenAI.
   * Inspect and validate the resulting JSON.

---

## Customization

You can easily adapt this sample:

* **Use different documents**
  Swap in contracts, receipts, or other PDFs and adjust the schema accordingly.

* **Extend the schema**
  Add fields such as `currency`, `tax_amount`, or `line_items`. Update the
  few-shot examples so the model learns to populate the new fields.

* **Filter or pre-process fragments**
  Use fragment metadata (e.g., page numbers, types) to drop noisy sections like
  footers or legal boilerplate before building `document_text`.

* **Change the model**
  Swap `"gpt-4o-mini"` for a different OpenAI model, or adjust the code to use
  a different provider via Outlines.
