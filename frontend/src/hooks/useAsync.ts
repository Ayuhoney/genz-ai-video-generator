import { useEffect, useRef, useState } from 'react'

interface AsyncState<T> {
  data: T | null
  error: string | null
  isLoading: boolean
}

export function useAsync<T>(loader: () => Promise<T>, depsKey: string) {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    error: null,
    isLoading: true,
  })
  const [tick, setTick] = useState(0)
  const [activeKey, setActiveKey] = useState(depsKey)
  const loaderRef = useRef(loader)

  useEffect(() => {
    loaderRef.current = loader
  })

  if (depsKey !== activeKey) {
    setActiveKey(depsKey)
    setState((prev) => ({ ...prev, isLoading: true, error: null }))
  }

  useEffect(() => {
    let cancelled = false

    void loaderRef
      .current()
      .then((data) => {
        if (!cancelled) {
          setState({ data, error: null, isLoading: false })
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setState({
            data: null,
            error:
              error instanceof Error ? error.message : 'Something went wrong.',
            isLoading: false,
          })
        }
      })

    return () => {
      cancelled = true
    }
  }, [depsKey, tick])

  return {
    ...state,
    reload: () => {
      setState((prev) => ({ ...prev, isLoading: true, error: null }))
      setTick((value) => value + 1)
    },
    setData: (data: T) => setState((prev) => ({ ...prev, data })),
  }
}
