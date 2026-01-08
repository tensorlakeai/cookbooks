import { NextRequest } from "next/server"

export async function POST(request: NextRequest) {
  const apiKey = request.headers.get("x-api-key")

  if (!apiKey) {
    return new Response(JSON.stringify({ error: "API key is required" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    })
  }

  const { query } = await request.json()

  if (!query) {
    return new Response(JSON.stringify({ error: "Query is required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    })
  }

  // Start the main request
  const response = await fetch(
    "https://api.tensorlake.ai/applications/finance_query",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(query),
    }
  )

  if (!response.ok) {
    const errorText = await response.text()
    return new Response(
      JSON.stringify({ error: `Tensorlake API error: ${errorText}` }),
      {
        status: response.status,
        headers: { "Content-Type": "application/json" },
      }
    )
  }

  // Create a combined stream that reads the main response and polls logs for progress
  const combinedStream = createCombinedStream(
    response.body!,
    apiKey,
    "finance_query"
  )

  return new Response(combinedStream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    },
  })
}

function createCombinedStream(
  mainStream: ReadableStream<Uint8Array>,
  apiKey: string,
  application: string
): ReadableStream<Uint8Array> {
  const decoder = new TextDecoder()
  const encoder = new TextEncoder()
  let requestId: string | null = null
  let isFinished = false
  let lastLogToken: string | null = null
  let pollingInterval: ReturnType<typeof setInterval> | null = null

  return new ReadableStream({
    async start(controller) {
      const mainReader = mainStream.getReader()
      let buffer = ""

      // Start polling logs when we have a request ID
      const startLogPolling = () => {
        if (!requestId || pollingInterval) return

        pollingInterval = setInterval(async () => {
          if (isFinished || !requestId) {
            if (pollingInterval) clearInterval(pollingInterval)
            return
          }

          try {
            const logsUrl = new URL(
              `https://api.tensorlake.ai/applications/${application}/logs`
            )
            logsUrl.searchParams.set("requestId", requestId)
            logsUrl.searchParams.set("tail", "20")
            if (lastLogToken) {
              logsUrl.searchParams.set("afterToken", lastLogToken)
            }

            const logsResponse = await fetch(logsUrl.toString(), {
              headers: {
                Authorization: `Bearer ${apiKey}`,
              },
            })

            if (logsResponse.ok) {
              const logsData = await logsResponse.json()

              // Process logs for progress updates
              for (const log of logsData.logs || []) {
                // Extract progress updates from logAttributes
                if (log.logAttributes) {
                  try {
                    const attrs = JSON.parse(log.logAttributes)
                    if (attrs.data?.RequestProgressUpdated) {
                      const progressEvent = `data: {"Progress": ${JSON.stringify(
                        attrs.data
                      )}}\n\n`
                      controller.enqueue(encoder.encode(progressEvent))
                    }
                  } catch {
                    // Not JSON, skip
                  }
                }

                // Also send log body messages (agent outputs)
                if (log.body && log.body.startsWith("[Agent]")) {
                  const logEvent = `data: {"AgentLog": {"message": ${JSON.stringify(
                    log.body
                  )}}}\n\n`
                  controller.enqueue(encoder.encode(logEvent))
                }
              }

              if (logsData.nextToken) {
                lastLogToken = logsData.nextToken
              }
            }
          } catch {
            // Ignore polling errors
          }
        }, 1000) // Poll every second
      }

      const readMain = async () => {
        while (true) {
          const { done, value } = await mainReader.read()
          if (done) break

          const chunk = decoder.decode(value, { stream: true })
          buffer += chunk

          // Forward the chunk to client
          controller.enqueue(value)

          // Try to extract request_id from RequestStarted event
          if (!requestId) {
            const match = buffer.match(
              /data:\s*\{\s*"RequestStarted"\s*:\s*\{[^}]*"request_id"\s*:\s*"([^"]+)"/
            )
            if (match) {
              requestId = match[1]
              startLogPolling()
            }
          }

          // Check for request finished
          if (chunk.includes('"RequestFinished"')) {
            isFinished = true
          }
        }

        // Cleanup
        isFinished = true
        if (pollingInterval) clearInterval(pollingInterval)
        controller.close()
      }

      readMain().catch((err) => {
        isFinished = true
        if (pollingInterval) clearInterval(pollingInterval)
        controller.error(err)
      })
    },
  })
}
