# Finance Dashboard

A Next.js web application for analyzing bank and credit card statements using Tensorlake's AI-powered finance apps.

## Features

- **PDF Statement Upload**: Drag-and-drop PDF statements for automatic parsing and categorization
- **Natural Language Queries**: Ask questions about your financial data in plain English
- **Chart Generation**: Generate spending visualizations from your data
- **Real-time Progress**: See live progress updates as your statements are processed

## Prerequisites

- Node.js 18+
- A [Tensorlake](https://tensorlake.ai) account
- Tensorlake CLI (`pip install tensorlake`)

## Deploy Tensorlake Apps

Before running the web app, you need to deploy the backend Tensorlake applications.

### 1. Set up environment variables

```bash
# Set your Tensorlake API key
export TENSORLAKE_API_KEY='your-tensorlake-api-key'
```

### 2. Configure secrets in Tensorlake

The backend apps need access to a PostgreSQL database and the Anthropic API:

```bash
tensorlake secrets set "DATABASE_URL=postgresql://user:password@host:5432/database"
tensorlake secrets set "ANTHROPIC_API_KEY=your-anthropic-api-key"
```

### 3. Deploy the apps

From the `personal_finance` directory:

```bash
cd /path/to/personal_finance
tensorlake deploy app.py
```

This deploys two applications:
- `finance_analyzer` - Parses PDF statements and stores transactions
- `finance_query` - Answers natural language questions about your data

## Run the Web App Locally

### 1. Install dependencies

```bash
cd web
npm install
```

### 2. Start the development server

```bash
npm run dev
```

### 3. Open the app

Navigate to [http://localhost:3000](http://localhost:3000) in your browser.

### 4. Configure your API key

Enter your Tensorlake API key in the configuration bar at the top of the page. The key is stored in your browser's local storage.

## Usage

### Upload a Statement

1. Drag and drop a PDF bank or credit card statement onto the upload zone
2. Watch the progress as the AI extracts and categorizes transactions
3. View the summary showing total spending, income, and category breakdown

### Query Your Data

Enter natural language questions like:
- "What did I spend on groceries last month?"
- "Show my top 5 merchants by spending"
- "Draw a bar chart of expenses by category"
- "What's my average daily spending?"

## Tech Stack

- [Next.js 14](https://nextjs.org/) (App Router)
- [TypeScript](https://www.typescriptlang.org/)
- [Tailwind CSS](https://tailwindcss.com/)
- [shadcn/ui](https://ui.shadcn.com/)
- [Tensorlake](https://tensorlake.ai/) for backend AI processing

## Project Structure

```
web/
├── app/
│   ├── api/
│   │   ├── upload/route.ts    # Proxy for statement uploads
│   │   └── query/route.ts     # Proxy for natural language queries
│   ├── layout.tsx
│   ├── page.tsx               # Main dashboard
│   └── globals.css
├── components/
│   ├── api-key-input.tsx      # API key configuration
│   ├── file-upload.tsx        # PDF upload with drag-and-drop
│   ├── query-box.tsx          # Natural language query input
│   └── result-display.tsx     # Results with tables and charts
├── lib/
│   ├── use-api-key.ts         # Hook for localStorage API key
│   └── utils.ts
└── README.md
```

## License

MIT
