"use client"

import React from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { FileText, AlertCircle, CheckCircle2 } from "lucide-react"

interface ResultDisplayProps {
  title: string
  result: {
    success: boolean
    data?: Record<string, unknown>
    error?: string
  } | null
}

export function ResultDisplay({ title, result }: ResultDisplayProps) {
  // Debug logging
  console.log("ResultDisplay called with result:", result)
  if (result?.data) {
    console.log("ResultDisplay data keys:", Object.keys(result.data))
    console.log("Full data object:", JSON.stringify(result.data).slice(0, 500))
    console.log("files_created value:", result.data.files_created)
  }

  if (!result) {
    return (
      <Card className="glass-card border-white/10">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-muted-foreground">
            <FileText className="h-5 w-5" />
            {title}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Results will appear here after processing.
          </p>
        </CardContent>
      </Card>
    )
  }

  if (!result.success) {
    return (
      <Card className="glass-card border-destructive/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-destructive">
            <AlertCircle className="h-5 w-5" />
            Error
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">{result.error}</p>
        </CardContent>
      </Card>
    )
  }

  const data = result.data

  // Handle query results (has answer field)
  if (data?.answer) {
    return (
      <Card className="glass-card border-white/10">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-green-500" />
            Query Result
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="prose prose-sm dark:prose-invert max-w-none">
            <div
              dangerouslySetInnerHTML={{
                __html: formatMarkdown(data.answer as string),
              }}
            />
          </div>

          {typeof data.sql_query === "string" && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                SQL Query:
              </p>
              <pre className="bg-muted p-3 rounded text-xs overflow-x-auto">
                {data.sql_query}
              </pre>
            </div>
          )}

          {Array.isArray(data.raw_results) && data.raw_results.length > 0 && (
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">
                Raw Data:
              </p>
              <div className="bg-muted p-3 rounded text-xs overflow-x-auto max-h-48 overflow-y-auto">
                <pre>{JSON.stringify(data.raw_results, null, 2)}</pre>
              </div>
            </div>
          )}

          {/* Render charts/images */}
          {renderImages(data.files_created)}
        </CardContent>
      </Card>
    )
  }

  // Handle upload results (has categories_summary, total_spending, etc.)
  if (data?.success && data?.total_transactions !== undefined) {
    return (
      <Card className="glass-card border-white/10">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-green-500" />
            Statement Processed
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Account</p>
              <p className="font-medium">{data.account_name as string}</p>
              <p className="text-sm text-muted-foreground">
                {data.account_holder as string}
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-xs text-muted-foreground">Statement Date</p>
              <p className="font-medium">{data.statement_date as string}</p>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div className="text-center p-3 bg-muted rounded-lg">
              <p className="text-2xl font-bold">
                {data.total_transactions as number}
              </p>
              <p className="text-xs text-muted-foreground">Transactions</p>
            </div>
            <div className="text-center p-3 bg-red-50 dark:bg-red-950 rounded-lg">
              <p className="text-2xl font-bold text-red-600">
                ${(data.total_spending as number)?.toFixed(2)}
              </p>
              <p className="text-xs text-muted-foreground">Total Spending</p>
            </div>
            <div className="text-center p-3 bg-green-50 dark:bg-green-950 rounded-lg">
              <p className="text-2xl font-bold text-green-600">
                ${(data.total_income as number)?.toFixed(2)}
              </p>
              <p className="text-xs text-muted-foreground">Total Income</p>
            </div>
          </div>

          {typeof data.categories_summary === "object" &&
            data.categories_summary !== null && (
              <div className="space-y-2">
                <p className="text-sm font-medium">Spending by Category</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(
                    data.categories_summary as Record<string, number>
                  )
                    .sort(([, a], [, b]) => b - a)
                    .map(([category, amount]) => (
                      <Badge key={category} variant="secondary">
                        {category}: ${amount.toFixed(2)}
                      </Badge>
                    ))}
                </div>
              </div>
            )}
        </CardContent>
      </Card>
    )
  }

  // Generic result display - also render images if present
  return (
    <Card className="glass-card border-white/10">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-green-500" />
          Result
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <pre className="bg-muted p-3 rounded text-xs overflow-x-auto max-h-96 overflow-y-auto">
          {JSON.stringify(data, null, 2)}
        </pre>
        {renderImages(data?.files_created)}
      </CardContent>
    </Card>
  )
}

function renderImages(filesCreated: unknown): React.ReactNode {
  console.log("renderImages called with:", filesCreated)

  if (!filesCreated || !Array.isArray(filesCreated) || filesCreated.length === 0) {
    console.log("No files to render")
    return null
  }

  // Format from app.py execute_code:
  // { filename: string, content_type: "text" | "binary", content?: string, content_base64?: string }
  const imageFiles = filesCreated.filter((file) => {
    const f = file as { filename?: string }
    if (!f.filename) return false
    const ext = f.filename.split(".").pop()?.toLowerCase()
    return ["png", "jpg", "jpeg", "gif", "svg"].includes(ext || "")
  })

  console.log("Image files found:", imageFiles.length)

  if (imageFiles.length === 0) return null

  return (
    <div className="space-y-4 mt-4">
      <p className="text-sm font-medium text-muted-foreground">Charts:</p>
      <div className="grid gap-4">
        {imageFiles.map((file, index) => {
          const fileData = file as {
            filename: string
            content_type: string
            content?: string
            content_base64?: string
          }

          console.log("Rendering file:", fileData.filename, "content_type:", fileData.content_type)
          console.log("Has content_base64:", !!fileData.content_base64)
          console.log("content_base64 length:", fileData.content_base64?.length)

          // Get base64 data from content_base64 field
          const base64Data = fileData.content_base64
          if (!base64Data) {
            console.log("No base64 data for", fileData.filename)
            return null
          }

          // Determine MIME type from filename
          const ext = fileData.filename.split(".").pop()?.toLowerCase()
          const mimeType =
            ext === "jpg" || ext === "jpeg"
              ? "image/jpeg"
              : ext === "gif"
              ? "image/gif"
              : ext === "svg"
              ? "image/svg+xml"
              : "image/png"

          const imgSrc = `data:${mimeType};base64,${base64Data}`
          console.log("Image src length:", imgSrc.length)

          return (
            <div key={index} className="space-y-2">
              <p className="text-sm text-muted-foreground">{fileData.filename}</p>
              <img
                src={imgSrc}
                alt={fileData.filename}
                className="max-w-full rounded-lg border border-white/10 bg-white"
                onError={(e) => console.error("Image failed to load:", e)}
                onLoad={() => console.log("Image loaded successfully")}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}

function formatMarkdown(text: string): string {
  // Check for markdown tables and convert them
  const lines = text.split("\n")
  const result: string[] = []
  let inTable = false
  let tableRows: string[] = []

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()

    // Check if this line is a table row (starts and ends with |)
    if (line.startsWith("|") && line.endsWith("|")) {
      // Skip separator rows (|---|---|)
      if (/^\|[\s\-:|]+\|$/.test(line)) {
        continue
      }

      if (!inTable) {
        inTable = true
        tableRows = []
      }
      tableRows.push(line)
    } else {
      // End of table, render it
      if (inTable && tableRows.length > 0) {
        result.push(renderTable(tableRows))
        tableRows = []
        inTable = false
      }

      // Process non-table line
      if (line) {
        let processed = line
          .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
          .replace(/\*(.*?)\*/g, "<em>$1</em>")
          .replace(/`(.*?)`/g, "<code class='bg-white/10 px-1 rounded'>$1</code>")

        if (processed.startsWith("## ")) {
          processed = `<h3 class="text-lg font-semibold mt-4 mb-2">${processed.slice(3)}</h3>`
        } else if (processed.startsWith("# ")) {
          processed = `<h2 class="text-xl font-bold mt-4 mb-2">${processed.slice(2)}</h2>`
        } else {
          processed = `<p class="mb-2">${processed}</p>`
        }
        result.push(processed)
      } else {
        result.push("<br />")
      }
    }
  }

  // Handle table at end of text
  if (inTable && tableRows.length > 0) {
    result.push(renderTable(tableRows))
  }

  return result.join("")
}

function renderTable(rows: string[]): string {
  if (rows.length === 0) return ""

  const parseRow = (row: string): string[] => {
    return row
      .slice(1, -1) // Remove leading and trailing |
      .split("|")
      .map((cell) => cell.trim())
  }

  const headerCells = parseRow(rows[0])
  const bodyRows = rows.slice(1)

  let html = `<div class="overflow-x-auto my-4"><table class="w-full text-sm border-collapse">`

  // Header
  html += `<thead><tr class="border-b border-white/20">`
  for (const cell of headerCells) {
    html += `<th class="text-left py-2 px-3 font-semibold text-muted-foreground">${cell}</th>`
  }
  html += `</tr></thead>`

  // Body
  html += `<tbody>`
  for (const row of bodyRows) {
    const cells = parseRow(row)
    html += `<tr class="border-b border-white/10 hover:bg-white/5">`
    for (const cell of cells) {
      // Highlight amounts (cells starting with $)
      const cellClass = cell.startsWith("$")
        ? "py-2 px-3 font-mono text-green-400"
        : "py-2 px-3"
      html += `<td class="${cellClass}">${cell}</td>`
    }
    html += `</tr>`
  }
  html += `</tbody></table></div>`

  return html
}
