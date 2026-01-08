"use client"

import { useState } from "react"
import { useApiKey } from "@/lib/use-api-key"
import { ApiKeyInput } from "@/components/api-key-input"
import { FileUpload, type UploadResult } from "@/components/file-upload"
import { QueryBox, type QueryResult } from "@/components/query-box"
import { ResultDisplay } from "@/components/result-display"
import { Wallet } from "lucide-react"

export default function Home() {
  const { apiKey, setApiKey, isLoaded } = useApiKey()
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null)
  const [queryResult, setQueryResult] = useState<QueryResult | null>(null)

  if (!isLoaded) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-pulse text-muted-foreground">Loading...</div>
      </div>
    )
  }

  return (
    <main className="min-h-screen">
      {/* Header */}
      <header className="glass-card border-b border-white/10">
        <div className="container mx-auto px-4 py-6">
          <div className="flex items-center gap-4 mb-6">
            <div className="p-3 rounded-xl bg-gradient-to-br from-purple-500 to-cyan-500 glow">
              <Wallet className="h-8 w-8 text-white" />
            </div>
            <div>
              <h1 className="text-3xl font-bold gradient-text">Finance Dashboard</h1>
              <p className="text-sm text-muted-foreground">
                Upload statements and query your financial data
              </p>
            </div>
          </div>
          <ApiKeyInput apiKey={apiKey} onApiKeyChange={setApiKey} />
        </div>
      </header>

      {/* Main Content */}
      <div className="container mx-auto px-4 py-8">
        <div className="grid lg:grid-cols-2 gap-8">
          {/* Left Column - Upload */}
          <div className="space-y-6">
            <FileUpload apiKey={apiKey} onResult={setUploadResult} />
            <ResultDisplay title="Upload Result" result={uploadResult} />
          </div>

          {/* Right Column - Query */}
          <div className="space-y-6">
            <QueryBox apiKey={apiKey} onResult={setQueryResult} />
            <ResultDisplay title="Query Result" result={queryResult} />
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer className="glass-card border-t border-white/10 mt-auto">
        <div className="container mx-auto px-4 py-4 text-center text-sm text-muted-foreground">
          Powered by{" "}
          <a
            href="https://tensorlake.ai"
            target="_blank"
            rel="noopener noreferrer"
            className="gradient-text font-semibold hover:opacity-80 transition-opacity"
          >
            Tensorlake
          </a>
        </div>
      </footer>
    </main>
  )
}
