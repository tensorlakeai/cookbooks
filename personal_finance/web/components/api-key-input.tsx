"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { KeyRound, Eye, EyeOff, Check } from "lucide-react"

interface ApiKeyInputProps {
  apiKey: string
  onApiKeyChange: (key: string) => void
}

export function ApiKeyInput({ apiKey, onApiKeyChange }: ApiKeyInputProps) {
  const [showKey, setShowKey] = useState(false)
  const [inputValue, setInputValue] = useState(apiKey)

  const handleSave = () => {
    onApiKeyChange(inputValue)
  }

  const isChanged = inputValue !== apiKey

  return (
    <div className="flex items-center gap-3 p-4 bg-white/5 rounded-lg border border-white/10">
      <KeyRound className="h-5 w-5 text-primary shrink-0" />
      <div className="flex-1 flex items-center gap-2">
        <Input
          type={showKey ? "text" : "password"}
          placeholder="Enter your Tensorlake API key"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          className="font-mono text-sm"
        />
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setShowKey(!showKey)}
          type="button"
        >
          {showKey ? (
            <EyeOff className="h-4 w-4" />
          ) : (
            <Eye className="h-4 w-4" />
          )}
        </Button>
        {isChanged && (
          <Button size="sm" onClick={handleSave}>
            <Check className="h-4 w-4 mr-1" />
            Save
          </Button>
        )}
      </div>
      {apiKey && !isChanged && (
        <span className="text-xs text-green-500 font-medium">
          API key configured
        </span>
      )}
    </div>
  )
}
