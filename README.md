# Tensorlake Cookbook 📚

Welcome to the Tensorlake Cookbook!
This repository contains a collection of sample notebooks, sample applications to help you get started with document AI and Tensrolake serverless apps.

---

## Overview

- This cookbook provides a range of small, focused examples demonstrating how to parse real-world documents with **Tensorlake Document AI** and use the Tensorlake serverless platform to deploy apps.

- Each sample lives in its **own directory** and comes with its **own README** to help you understand, run, and extend it quickly.

---

## Getting Started

To get started with any of the samples, follow these steps:

1. **Clone the repository**

   ```bash
   git clone https://github.com/tensorlakeai/cookbooks
   cd cookbooks
   ```

2. **Navigate to a sample directory**

   For example, to open the Outlines + Tensorlake example:

   ```bash
   cd using-outline-with-tensorlake
   ```

3. **Follow the sample’s README**

   Each sample directory has its own `README.md` with detailed instructions for
   installing dependencies, setting environment variables, and running the notebook or app.

---

## Sample Cookbooks 🧪

Here are the samples currently included in this repository:

| Sample                                                        | Description                                                                                                                                             | Link                                                               |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| **Using Outlines with Tensorlake (Schema-Enforced Invoices)** | Parse an invoice PDF with Tensorlake, then use Outlines + OpenAI to generate JSON that *must* match a Pydantic `Invoice` schema (no malformed outputs). | [`using-outline-with-tensorlake`](./using-outline-with-tensorlake/) |


---

## Contributing 🤝

Contributions are welcome!

**Typical ways to contribute:**

* Add a new sample under a new directory (e.g., `contracts-structured-extraction/`)
* Improve documentation or comments in existing notebooks
* Share bug fixes or small enhancements (via PR)

**If you add a new sample, please:**

1. Create a folder at the repo root, e.g. `my-new-sample/`
2. Add:

   * A notebook or application
   * A `README.md` explaining what it does and how to run it
3. Update the **Sample Cookbooks** table in this root `README.md` with:

   * Sample name
   * Short description
   * Link to the folder

4. Raise a PR with changes.

---

## License

This project is intended to be licensed under the **MIT License**

See the [`LICENSE`](./LICENSE) file for full details once you’ve added it.
