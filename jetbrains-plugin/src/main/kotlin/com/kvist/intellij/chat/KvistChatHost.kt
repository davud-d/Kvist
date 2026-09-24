package com.kvist.intellij.chat

import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.JsonNull
import com.google.gson.JsonObject
import com.google.gson.JsonParseException
import com.google.gson.JsonParser
import com.intellij.execution.ExecutionException
import com.intellij.ide.BrowserUtil
import com.intellij.openapi.Disposable
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.logger
import com.intellij.openapi.fileChooser.FileChooser
import com.intellij.openapi.fileChooser.FileChooserDescriptorFactory
import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.project.Project
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.vfs.VfsUtilCore
import com.kvist.intellij.runtime.KvistLauncher
import com.kvist.intellij.runtime.KvistTarget
import java.io.IOException
import java.nio.file.InvalidPathException
import java.nio.file.Path
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

// The newest events of a session: enough for well over a hundred turns of a typical run.
private const val HISTORY_LIMIT = 1000

// The provider ids offered on first run, in priority order (same list as the VS Code panel).
private val FIRST_RUN_PROVIDERS = listOf("anymodel", "openai", "anthropic", "deepseek", "openrouter", "omniroute", "freeflow")

private const val CORE_STOP_WAIT_SECONDS = 5L

/**
 * The plugin side of the panel: answers the page's `ToHost` messages
 * (`vscode-extension/src/messages.ts`) and sends `ToWebview` ones back.
 * A Kotlin port of `vscode-extension/src/chatView.ts`.
 */
internal class KvistChatHost(
    private val project: Project,
    private val runScript: (String) -> Unit,
) : Disposable, KvistCoreListener {
    private val gson = Gson()
    private val postLock = Any()
    private val startLock = Any()
    private var ready = false
    private var disposed = false
    private val queued = mutableListOf<JsonObject>()
    @Volatile private var core: KvistCoreProcess? = null
    @Volatile private var workspace: Path? = null

    /**
     * A new page starts from scratch and gets a core of its own, like a reloaded VS Code
     * window: the old core's `hello` is gone, so the new page could never learn its state.
     */
    fun pageLoading() {
        synchronized(postLock) {
            ready = false
            queued.clear()
        }
        val previous = core
        core = null
        previous?.stop()
    }

    fun newSession() {
        post(message("invoke") {
            addProperty("name", "new")
            add("args", jsonArray(emptyList()))
        })
    }

    fun showSettings() {
        post(message("showSettings"))
    }

    fun onPageMessage(json: String) {
        val message = try {
            JsonParser.parseString(json) as? JsonObject
        }
        catch (_: JsonParseException) {
            null
        } ?: return
        when (message.stringField("type")) {
            "ready" -> {
                markReady()
                background { checkAndStart() }
            }
            "setProvider" -> background {
                setProvider(message.stringField("provider").orEmpty(), message.stringField("apiKey").orEmpty(), message.stringField("model").orEmpty())
            }
            "listModels" -> background { listModels(message.stringField("provider").orEmpty(), message.stringField("apiKey").orEmpty()) }
            "openRegistration" -> background { openRegistration(message.stringField("provider").orEmpty()) }
            "submit" -> send { it.send(message.stringField("text").orEmpty()) }
            "command" -> send { it.sendCommand(message.stringField("name").orEmpty(), message.stringList("args")) }
            "respond" -> respond(message)
            "cancel" -> send { it.cancel() }
            "pickFiles" -> ApplicationManager.getApplication().invokeLater { pickFiles() }
            "getEnvironment" -> post(environmentMessage(environment()))
            "setEnvironment" -> background { applyEnvironment(message.get("env")) }
            "getConfig" -> background { sendConfig() }
            "getSessions" -> background { sendSessions() }
            "getHistory" -> background { sendHistory(message.stringField("name").orEmpty()) }
            "openFile" -> ApplicationManager.getApplication().invokeLater {
                openFile(message.stringField("path").orEmpty(), message.intField("line"))
            }
            "openLink" -> openLink(message.stringField("url").orEmpty())
        }
    }

    override fun onEvent(event: JsonObject) {
        post(message("event") { add("event", event) })
    }

    override fun onDiagnostic(text: String) {
        post(message("diagnostic") { addProperty("text", text) })
    }

    override fun onFault(message: String) {
        post(fault(message))
    }

    override fun onExit(code: Int?, expected: Boolean) {
        post(message("exit") {
            addProperty("expected", expected)
            addProperty("code", code)
        })
    }

    override fun dispose() {
        synchronized(postLock) {
            disposed = true
            queued.clear()
        }
        core?.stop()
        core = null
    }

    private fun post(message: JsonObject) {
        synchronized(postLock) {
            if (disposed) return
            if (!ready) {
                queued += message
                return
            }
            runScript(deliverCall(message))
        }
    }

    private fun markReady() {
        synchronized(postLock) {
            ready = true
            queued.forEach { runScript(deliverCall(it)) }
            queued.clear()
        }
    }

    private fun deliverCall(message: JsonObject): String {
        return "window.kvistHost && window.kvistHost.receive && window.kvistHost.receive(${gson.toJson(message)});"
    }

    private fun fault(text: String): JsonObject = message("fault") { addProperty("message", text) }

    private fun background(action: () -> Unit) {
        ApplicationManager.getApplication().executeOnPooledThread(Runnable { action() })
    }

    private fun send(action: (KvistCoreProcess) -> Unit) {
        val running = core?.takeIf { it.running }
        if (running == null) {
            post(fault("The Kvist core is not running. Use Reload in the Kvist tool window title to restart it."))
            return
        }
        try {
            action(running)
        }
        catch (exception: IOException) {
            post(fault(exception.message ?: "Kvist core is not accepting input."))
        }
    }

    private fun respond(message: JsonObject) {
        val requestId = message.stringField("requestId") ?: return
        val answer = message.get("answer")
        val value: Any? = when {
            answer == null || !answer.isJsonPrimitive -> null
            answer.asJsonPrimitive.isBoolean -> answer.asBoolean
            answer.asJsonPrimitive.isString -> answer.asString
            else -> null
        }
        send { it.respond(requestId, value, message.booleanField("remember") == true) }
    }

    private fun target(): KvistTarget? {
        val resolved = KvistLauncher.prepareTarget(project) { text -> post(fault(text)) } ?: return null
        workspace = resolved.workspace
        return resolved
    }

    // The core refuses to start without a usable provider (UI_API.md §11), so a first-ever
    // run is caught here, before spawning it, rather than shown as a startup crash.
    private fun checkAndStart() {
        val target = target() ?: return
        val info = KvistCli.readJson(target, listOf("--config-info"))
        if (info != null) {
            val providers = info.objectList("providers")
            val current = providers.find { it.stringField("id") == info.stringField("saved_provider") }
            if (current?.booleanField("has_credentials") != true) {
                val offered = FIRST_RUN_PROVIDERS.mapNotNull { id -> providers.find { it.stringField("id") == id } }
                post(message("needsSetup") { add("providers", jsonArrayOf(offered)) })
                return
            }
            post(message("config") { add("info", info) })
        }
        startCore(target)
    }

    private fun startCore(target: KvistTarget) {
        synchronized(startLock) {
            if (core?.running == true) return
            // A window opens its own session, so a new run is never planned against an older launch's history.
            KvistCli.sessionOp(target, "launch")
            val process = KvistCoreProcess(target.binaryPath, target.workspace, environment(), this)
            try {
                process.start()
            }
            catch (exception: ExecutionException) {
                LOG.warn("Failed to start the Kvist core", exception)
                post(fault("Failed to start Kvist: ${exception.message}"))
                return
            }
            core = process
        }
    }

    private fun configOp(op: String, payload: JsonObject): JsonObject {
        val target = target() ?: return KvistCli.failure("Kvist is not ready.")
        return KvistCli.configOp(target, op, payload)
    }

    private fun setProvider(provider: String, apiKey: String, model: String) {
        val result = configOp("set-provider", JsonObject().apply {
            addProperty("provider", provider)
            addProperty("api_key", apiKey)
            addProperty("model", model)
        })
        val ok = result.booleanField("ok") == true
        post(message("setupResult") {
            addProperty("ok", ok)
            result.stringField("error")?.let { addProperty("error", it) }
        })
        if (ok) {
            checkAndStart()
        }
    }

    // The first-run screen's model field for a router provider: the live catalog for the
    // key just typed, before that key is saved.
    private fun listModels(provider: String, apiKey: String) {
        val result = configOp("list-models", JsonObject().apply {
            addProperty("provider", provider)
            addProperty("api_key", apiKey)
        })
        post(message("modelList") {
            addProperty("provider", provider)
            addProperty("ok", result.booleanField("ok") == true)
            add("models", jsonArray(result.stringList("models")))
            result.stringField("error")?.let { addProperty("error", it) }
        })
    }

    private fun openRegistration(provider: String) {
        val result = configOp("open-registration", JsonObject().apply { addProperty("provider", provider) })
        post(message("registrationOpened") {
            addProperty("provider", provider)
            addProperty("opened", result.booleanField("opened") == true)
            addProperty("url", result.stringField("url"))
        })
    }

    private fun environment(): Map<String, String> {
        val saved = KvistEnvironment.load()
        if (saved.rejected.isNotEmpty()) {
            post(fault("Ignored invalid entries in \"kvist.environment\": ${saved.rejected.joinToString(", ")}"))
        }
        return saved.valid
    }

    private fun environmentMessage(values: Map<String, String>): JsonObject {
        return message("environment") { add("env", KvistEnvironment.toJson(values)) }
    }

    // The core reads its environment once at launch, so a change means a new core. The
    // conversation stays on disk in its session; only the running process is replaced.
    private fun applyEnvironment(requested: JsonElement?) {
        val split = KvistEnvironment.split(requested)
        if (split.rejected.isNotEmpty()) {
            post(fault("Not saved, invalid variable names: ${split.rejected.joinToString(", ")}"))
            return
        }
        KvistEnvironment.save(split.valid)
        post(environmentMessage(split.valid))
        val previous = core
        core = null
        previous?.let { awaitStop(it) }
        target()?.let { startCore(it) }
    }

    private fun awaitStop(process: KvistCoreProcess) {
        try {
            process.stop().get(CORE_STOP_WAIT_SECONDS, TimeUnit.SECONDS)
        }
        catch (exception: TimeoutException) {
            LOG.warn("The previous Kvist core did not exit in $CORE_STOP_WAIT_SECONDS s", exception)
        }
        catch (exception: java.util.concurrent.ExecutionException) {
            LOG.warn("Waiting for the previous Kvist core failed", exception)
        }
        catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    private fun sendConfig() {
        val target = target() ?: return
        val info = KvistCli.readJson(target, listOf("--config-info")) ?: return
        post(message("config") { add("info", info) })
    }

    // "list" is "status" without the whole histories of every session.
    private fun sendSessions() {
        val target = target() ?: return
        val list = KvistCli.sessionOp(target, "list") ?: return
        val items = list.objectList("session_items").map { item ->
            JsonObject().apply {
                add("name", item.get("name") ?: JsonNull.INSTANCE)
                add("title", item.get("title") ?: JsonNull.INSTANCE)
                add("updated_at", item.get("updated_at") ?: JsonNull.INSTANCE)
            }
        }
        post(message("sessions") {
            add("info", JsonObject().apply {
                list.stringField("active_session")?.let { addProperty("active_session", it) }
                add("session_items", jsonArrayOf(items))
            })
        })
    }

    // What the user saw in a session, as the events the core recorded; the page replays them.
    private fun sendHistory(name: String) {
        val target = target() ?: return
        val reply = KvistCli.readJson(
            target,
            listOf("--workspace", target.workspace.toString(), "--session-op", "history", "--session-name", name, "--history-limit", HISTORY_LIMIT.toString()),
        )
        if (reply?.booleanField("ok") != true) {
            val detail = reply?.stringField("error")?.let { ": $it" }.orEmpty()
            post(fault("Could not load the history of \"$name\"$detail."))
            return
        }
        val events = reply.objectList("events").mapNotNull { it.get("event") }
        post(message("history") {
            addProperty("name", name)
            add("events", jsonArrayOf(events))
            addProperty("hasMore", reply.booleanField("has_more") == true)
        })
    }

    // Only the chosen names leave this function: the model gets names, not contents.
    private fun pickFiles() {
        val root = workspace?.let { LocalFileSystem.getInstance().findFileByNioFile(it) }
        if (root == null) {
            post(message("attached") { add("paths", jsonArray(emptyList())) })
            return
        }
        val descriptor = FileChooserDescriptorFactory.createMultipleFilesNoJarsDescriptor()
            .withRoots(root)
            .withTitle("Attach Files by Name")
            .withDescription("Their contents are not sent.")
        val paths = FileChooser.chooseFiles(descriptor, project, root)
            .mapNotNull { VfsUtilCore.getRelativePath(it, root) }
            // The agent may not read .env files, so attaching them would only invite a refusal.
            .filterNot { it.substringAfterLast('/').startsWith(".env") }
        post(message("attached") { add("paths", jsonArray(paths)) })
    }

    private fun openFile(relativePath: String, line: Int?) {
        val root = workspace ?: return
        val target = try {
            root.resolve(relativePath).normalize()
        }
        catch (_: InvalidPathException) {
            return
        }
        // The path comes from the agent's output, so it must not lead outside the workspace.
        if (!target.startsWith(root)) return
        val file = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(target) ?: return
        OpenFileDescriptor(project, file, maxOf(0, (line ?: 1) - 1), 0).navigate(true)
    }

    private fun openLink(url: String) {
        if (Regex("^https?://", RegexOption.IGNORE_CASE).containsMatchIn(url)) {
            BrowserUtil.browse(url)
        }
    }

    private companion object {
        val LOG = logger<KvistChatHost>()
    }
}
