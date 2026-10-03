package com.kvist.intellij.selection.llm

data class KvistLlmRequest(val systemPrompt: String, val userPrompt: String)

data class KvistLlmResponse(val text: String)

/** A failure whose message is short enough to show to the user as is. */
class KvistLlmException(message: String) : Exception(message)

interface KvistLlmClient {
    /** Cancelling the calling coroutine cancels the request. */
    suspend fun complete(request: KvistLlmRequest): KvistLlmResponse
}
