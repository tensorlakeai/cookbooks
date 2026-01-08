"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Textarea } from "@/components/ui/textarea"
import { Button } from "@/components/ui/button"
import { Search, Loader2, Send } from "lucide-react"

interface QueryBoxProps {
  apiKey: string
  onResult: (result: QueryResult) => void
}

export interface QueryResult {
  success: boolean
  data?: Record<string, unknown>
  error?: string
  events: SSEEvent[]
}

interface SSEEvent {
  type: string
  data: Record<string, unknown>
}

export function QueryBox({ apiKey, onResult }: QueryBoxProps) {
  const [query, setQuery] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [events, setEvents] = useState<SSEEvent[]>([])

  const processSSE = async (response: Response): Promise<QueryResult> => {
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

  const handleSubmit = async () => {
    if (!query.trim()) return

    if (!apiKey) {
      onResult({
        success: false,
        error: "Please configure your API key first",
        events: [],
      })
      return
    }

    setIsLoading(true)
    setEvents([])

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": apiKey,
        },
        body: JSON.stringify({ query }),
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.error || "Query failed")
      }

      const result = await processSSE(response)
      onResult(result)
    } catch (error) {
      onResult({
        success: false,
        error: error instanceof Error ? error.message : "Query failed",
        events,
      })
    } finally {
      setIsLoading(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      handleSubmit()
    }
  }

  return (
    <Card className="glass-card border-white/10">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Search className="h-5 w-5 text-primary" />
          Query Your Finances
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Textarea
            placeholder="Ask a question about your finances... (e.g., 'How much did I spend on DoorDash?' or 'Show me my monthly spending breakdown')"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={3}
            disabled={isLoading}
          />
          <p className="text-xs text-muted-foreground">
            Press Cmd/Ctrl + Enter to submit
          </p>
        </div>

        <Button onClick={handleSubmit} disabled={isLoading || !query.trim()}>
          {isLoading ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              Querying...
            </>
          ) : (
            <>
              <Send className="h-4 w-4 mr-2" />
              Ask Question
            </>
          )}
        </Button>

        {isLoading && events.length > 0 && (
          <div className="space-y-2">
            {/* Progress messages */}
            {(() => {
              // Get latest progress or agent log
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
            <div className="max-h-20 overflow-y-auto bg-white/5 rounded p-2 text-xs font-mono space-y-1">
              {events
                .filter((e) => e.type !== "Progress" && e.type !== "AgentLog")
                .slice(-3)
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
      </CardContent>
    </Card>
  )
}
