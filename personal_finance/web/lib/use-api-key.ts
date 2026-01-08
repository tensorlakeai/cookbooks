"use client"

import { useState, useEffect } from "react"

const API_KEY_STORAGE_KEY = "tensorlake_api_key"

export function useApiKey() {
  const [apiKey, setApiKeyState] = useState<string>("")
  const [isLoaded, setIsLoaded] = useState(false)

  useEffect(() => {
    const stored = localStorage.getItem(API_KEY_STORAGE_KEY)
    if (stored) {
      setApiKeyState(stored)
    }
    setIsLoaded(true)
  }, [])

  const setApiKey = (key: string) => {
    setApiKeyState(key)
    if (key) {
      localStorage.setItem(API_KEY_STORAGE_KEY, key)
    } else {
      localStorage.removeItem(API_KEY_STORAGE_KEY)
    }
  }

  return { apiKey, setApiKey, isLoaded }
}
