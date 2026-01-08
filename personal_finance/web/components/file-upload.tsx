"use client"

import { useCallback, useState } from "react"
import { useDropzone } from "react-dropzone"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Upload, FileText, Loader2, CheckCircle2, XCircle } from "lucide-react"
import { cn } from "@/lib/utils"

interface FileUploadProps {
  apiKey: string
  onResult: (result: UploadResult) => void
}

export interface UploadResult {
  success: boolean
  data?: Record<string, unknown>
  error?: string
  events: SSEEvent[]
}

interface SSEEvent {
  type: string
  data: Record<string, unknown>
}

export function FileUpload({ apiKey, onResult }: FileUploadProps) {
  const [isUploading, setIsUploading] = useState(false)
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [events, setEvents] = useState<SSEEvent[]>([])
  const [status, setStatus] = useState<"idle" | "uploading" | "success" | "error">("idle")

  const processSSE = async (response: Response): Promise<UploadResult> => {
    const reader = response.body?.getReader()
    const decoder = new TextDecoder()
    const collectedEvents: SSEEvent[] = []
    let finalResult: Record<string, unknown> | undefined

    if (!reader) {
      throw new Error("No response body")
    }

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      const chunk = decoder.decode(value, { stream: true })
      const lines = chunk.split("\n")

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          try {
            const data = JSON.parse(line.slice(6))
            const eventType = Object.keys(data)[0]
            const event: SSEEvent = { type: eventType, data: data[eventType] }
            collectedEvents.push(event)
            setEvents((prev) => [...prev, event])

            if (eventType === "RequestFinished") {
              finalResult = data.RequestFinished.output?.body
            }
          } catch {
            // Skip non-JSON lines
          }
        }
      }
    }

    return {
      success: true,
      data: finalResult,
      events: collectedEvents,
    }
  }

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      const file = acceptedFiles[0]
      if (!file) return

      if (!apiKey) {
        onResult({
          success: false,
          error: "Please configure your API key first",
          events: [],
        })
        return
      }

      setUploadedFile(file)
      setIsUploading(true)
      setStatus("uploading")
      setEvents([])

      try {
        const formData = new FormData()
        formData.append("file", file)

        const response = await fetch("/api/upload", {
          method: "POST",
          headers: {
            "x-api-key": apiKey,
          },
          body: formData,
        })

        if (!response.ok) {
          const error = await response.json()
          throw new Error(error.error || "Upload failed")
        }

        const result = await processSSE(response)
        setStatus("success")
        onResult(result)
      } catch (error) {
        setStatus("error")
        onResult({
          success: false,
          error: error instanceof Error ? error.message : "Upload failed",
          events,
        })
      } finally {
        setIsUploading(false)
      }
    },
    [apiKey, onResult, events]
  )

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
    },
    maxFiles: 1,
    disabled: isUploading,
  })

  return (
    <Card className="glass-card border-white/10">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Upload className="h-5 w-5 text-primary" />
          Upload Statement
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div
          {...getRootProps()}
          className={cn(
            "border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors",
            isDragActive
              ? "border-primary bg-primary/5"
              : "border-muted-foreground/25 hover:border-primary/50",
            isUploading && "pointer-events-none opacity-50"
          )}
        >
          <input {...getInputProps()} />
          {isUploading ? (
            <div className="flex flex-col items-center gap-2">
              <Loader2 className="h-10 w-10 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">
                Processing {uploadedFile?.name}...
              </p>
            </div>
          ) : isDragActive ? (
            <div className="flex flex-col items-center gap-2">
              <FileText className="h-10 w-10 text-primary" />
              <p className="text-sm">Drop your PDF here</p>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2">
              <Upload className="h-10 w-10 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                Drag & drop a PDF statement, or click to select
              </p>
              <p className="text-xs text-muted-foreground">
                Supports credit card and bank statements
              </p>
            </div>
          )}
        </div>

        {status !== "idle" && (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              {status === "uploading" && (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Processing...</span>
                </>
              )}
              {status === "success" && (
                <>
                  <CheckCircle2 className="h-4 w-4 text-green-600" />
                  <span className="text-green-600">Upload complete!</span>
                </>
              )}
              {status === "error" && (
                <>
                  <XCircle className="h-4 w-4 text-destructive" />
                  <span className="text-destructive">Upload failed</span>
                </>
              )}
            </div>

            {events.length > 0 && (
              <div className="space-y-2">
                {/* Progress messages */}
                {(() => {
                  const progressEvents = events.filter(
                    (e) => e.type === "Progress" || e.type === "AgentLog"
                  )
                  const latestProgress = progressEvents[progressEvents.length - 1]

                  if (!latestProgress) return null

                  if (latestProgress.type === "Progress") {
                    const progressData = latestProgress.data as {
                      RequestProgressUpdated?: {
                        message?: string
                        step?: string
                        total?: string
                      }
                    }
                    const progress = progressData?.RequestProgressUpdated
                    return progress?.message ? (
                      <div className="flex items-center gap-2 text-sm text-primary">
                        <div className="h-2 w-2 rounded-full bg-primary animate-pulse" />
                        <span>{progress.message}</span>
                        {progress.step && progress.total && (
                          <span className="text-xs text-muted-foreground">
                            ({progress.step}/{progress.total})
                          </span>
                        )}
                      </div>
                    ) : null
                  }

                  if (latestProgress.type === "AgentLog") {
                    const logData = latestProgress.data as { message?: string }
                    return logData?.message ? (
                      <div className="flex items-center gap-2 text-sm text-cyan-400">
                        <div className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
                        <span className="truncate">{logData.message}</span>
                      </div>
                    ) : null
                  }

                  return null
                })()}

                {/* Event log */}
                <div className="max-h-24 overflow-y-auto bg-white/5 rounded p-2 text-xs font-mono space-y-1">
                  {events
                    .filter((e) => e.type !== "Progress" && e.type !== "AgentLog")
                    .slice(-5)
                    .map((event, i) => {
                      const funcName = event.data.function_name as string | undefined
                      return (
                        <div key={i} className="text-muted-foreground">
                          {event.type}
                          {funcName ? `: ${funcName}` : ""}
                        </div>
                      )
                    })}
                </div>
              </div>
            )}
          </div>
        )}

        {status === "success" && (
          <Button
            variant="outline"
            onClick={() => {
              setStatus("idle")
              setUploadedFile(null)
              setEvents([])
            }}
          >
            Upload Another
          </Button>
        )}
      </CardContent>
    </Card>
  )
}
