package com.kvist.intellij.completion

import com.intellij.testFramework.LightVirtualFile
import com.kvist.intellij.runtime.KvistTarget
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test
import java.nio.file.Path

class CompletionFilePolicyTest {
    @Test fun `environment names are blocked in every path component and case`() {
        for (path in listOf(".env", ".env.local", ".ENVrc", "project/.EnV/config.py", "C:\\project\\.ENV.production")) {
            assertTrue(path, CompletionFilePolicy.isBlocked(path))
        }
        for (path in listOf("env", "environment.py", "project/my.env", "project/.venv/main.py")) {
            assertFalse(path, CompletionFilePolicy.isBlocked(path))
        }
    }

    @Test fun `virtual file canonical names cannot bypass the guard`() {
        val alias = object : LightVirtualFile("safe.py") {
            override fun getCanonicalPath(): String = "project/.ENV.local"
        }
        assertTrue(CompletionFilePolicy.isBlocked(alias))
        assertFalse(CompletionFilePolicy.isBlocked(LightVirtualFile("safe.py")))
    }

    @Test fun `missing paths retain lexical protection without reading contents`() {
        val workspace = Path.of("nonexistent-completion-policy-workspace")
        assertTrue(CompletionFilePolicy.isBlocked(workspace, ".env/settings.py"))
        assertTrue(CompletionFilePolicy.isBlocked(workspace.resolve(".ENV"), "safe.py"))
        assertFalse(CompletionFilePolicy.isBlocked(workspace, "safe.py"))
    }

    @Test fun `client rejects environment payloads before cache or process access`() = runBlocking {
        val client = KvistCompletionClient()
        val target = KvistTarget(Path.of("nonexistent-completion-binary"), Path.of("project"))
        for (path in listOf(".env", ".ENV.local", "nested/.env/config.py")) {
            val payload = CompletionPayload(path, "text", "synthetic prefix", "synthetic suffix")
            assertNull(client.cached(target, payload))
            assertTrue(client.stream(target, payload).toList().isEmpty())
        }
    }
}
