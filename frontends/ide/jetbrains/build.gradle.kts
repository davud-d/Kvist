import org.gradle.api.GradleException
import org.gradle.api.tasks.Exec
import org.gradle.api.tasks.Sync
import org.gradle.api.tasks.bundling.Zip
import java.io.File
import java.util.Locale
import java.util.zip.ZipFile

plugins {
    kotlin("jvm") version "2.2.21"
    id("org.jetbrains.intellij.platform")
}

group = providers.gradleProperty("group").get()
version = providers.gradleProperty("version").get()

dependencies {
    intellijPlatform {
        intellijIdea("2025.3")
        bundledPlugin("org.jetbrains.plugins.terminal")
        bundledPlugin("Git4Idea")
    }
}

kotlin {
    jvmToolchain(21)
}

// The UI is Swing plus a JCEF page, with no GUI Designer .form files, so there is
// nothing to instrument, and the task would download a Java compiler for nothing.
intellijPlatform {
    instrumentCode = false
}

val hostOsName = System.getProperty("os.name").lowercase(Locale.US)
val pluginDistributionDirectoryName = project.name

// The chat tool window shows the VS Code extension's webview. Its sources stay in
// frontends/ide/vscode/; the plugin takes the bundle that extension's own build produces.
val vscodeExtensionDir = layout.projectDirectory.dir("../vscode")
val webviewResourcesDir = layout.buildDirectory.dir("generated/webview")

fun npmCommand(vararg args: String): List<String> {
    return if (hostOsName.contains("win")) listOf("cmd", "/c", "npm", *args) else listOf("npm", *args)
}

val installWebviewDependencies = tasks.register<Exec>("installWebviewDependencies") {
    group = "build"
    description = "Installs the VS Code extension's npm dependencies when they are missing."
    workingDir = vscodeExtensionDir.asFile
    commandLine(npmCommand("ci", "--no-audit", "--no-fund"))
    onlyIf { !vscodeExtensionDir.dir("node_modules").asFile.isDirectory }
}

val buildWebview = tasks.register<Exec>("buildWebview") {
    group = "build"
    description = "Builds the VS Code extension, whose webview bundle the chat tool window loads."
    dependsOn(installWebviewDependencies)
    workingDir = vscodeExtensionDir.asFile
    commandLine(npmCommand("run", "build"))
    inputs.dir(vscodeExtensionDir.dir("webview"))
    inputs.dir(vscodeExtensionDir.dir("src"))
    // The panel also bundles the Ink app's shared modules (state, protocol, commands, …).
    inputs.files(fileTree(layout.projectDirectory.dir("../../tui/ink")) { include("*.ts") })
    inputs.files(vscodeExtensionDir.file("package.json"), vscodeExtensionDir.file("esbuild.mjs"))
    outputs.files(vscodeExtensionDir.file("out/webview.js"), vscodeExtensionDir.file("out/webview.css"))
}

val syncWebview = tasks.register<Sync>("syncWebview") {
    group = "build"
    description = "Copies the webview bundle and the logo into the plugin resources."
    dependsOn(buildWebview)
    from(vscodeExtensionDir.file("out/webview.js"), vscodeExtensionDir.file("out/webview.css"))
    from(vscodeExtensionDir.file("resources/kvist-logo.png"))
    into(webviewResourcesDir.map { it.dir("kvist-webview") })
}

sourceSets.main {
    resources.srcDir(webviewResourcesDir)
}

tasks.named("processResources") {
    dependsOn(syncWebview)
}

fun requiredBinariesForBuild(): List<File> = listOf(
    file("bin/win/kvist.exe"),
    file("bin/linux/kvist.bin"),
)

fun requiredBinariesForRunIde(): List<File> {
    return when {
        hostOsName.contains("win") -> listOf(file("bin/win/kvist.exe"))
        hostOsName.contains("linux") -> listOf(file("bin/linux/kvist.bin"))
        else -> emptyList()
    }
}

fun validateBinarySet(files: List<File>, context: String) {
    val missingFiles = files.filterNot { it.isFile }

    if (missingFiles.isNotEmpty()) {
        throw GradleException(
            buildString {
                appendLine("Missing required Kvist binaries for $context:")
                missingFiles.forEach { appendLine(" - ${it.invariantSeparatorsPath}") }
                appendLine("Expected files:")
                appendLine(" - bin/win/kvist.exe")
                append(" - bin/linux/kvist.bin")
            }
        )
    }
}

val kvistSandboxBinaries = copySpec {
    from("bin/win") {
        exclude("**/.gitkeep")
        into("$pluginDistributionDirectoryName/bin/win")
        includeEmptyDirs = false
    }

    from("bin/linux") {
        include("kvist.bin")
        into("$pluginDistributionDirectoryName/bin/linux")
        includeEmptyDirs = false
        filePermissions {
            unix("rwxr-xr-x")
        }
    }
}

val validateHostKvistBinary = tasks.register("validateHostKvistBinary") {
    group = "verification"
    description = "Ensures that the current host binary exists for local sandbox runs."
    notCompatibleWithConfigurationCache("Reads project-local binary files during task execution.")

    doLast {
        validateBinarySet(requiredBinariesForRunIde(), "the current host")
    }
}

val validateAllKvistBinaries = tasks.register("validateAllKvistBinaries") {
    group = "verification"
    description = "Ensures that all packaged Kvist binaries exist for plugin distribution builds."
    notCompatibleWithConfigurationCache("Reads project-local binary files during task execution.")

    doLast {
        validateBinarySet(requiredBinariesForRunIde(), "plugin packaging")

        val hostBinaries = requiredBinariesForRunIde()
        requiredBinariesForBuild()
            .filterNot { it in hostBinaries }
            .filterNot { it.isFile }
            .forEach { missing ->
                logger.warn(
                    "Optional Kvist binary is missing: ${missing.invariantSeparatorsPath}. " +
                        "The plugin will be built without support for that platform."
                )
            }
    }
}

tasks.named<Sync>("prepareSandbox") {
    dependsOn(validateHostKvistBinary)
    with(kvistSandboxBinaries)
}

tasks.named("runIde") {
    dependsOn(validateHostKvistBinary)
}

tasks.named<Zip>("buildPlugin") {
    dependsOn(validateHostKvistBinary)

    doLast {
        val archiveFile = archiveFile.get().asFile

        val hostEntry = when {
            hostOsName.contains("win") -> "$pluginDistributionDirectoryName/bin/win/kvist.exe"
            hostOsName.contains("linux") -> "$pluginDistributionDirectoryName/bin/linux/kvist.bin"
            else -> null
        }
        val optionalEntries = listOf(
            "$pluginDistributionDirectoryName/bin/win/kvist.exe",
            "$pluginDistributionDirectoryName/bin/linux/kvist.bin",
        ).filterNot { it == hostEntry }

        ZipFile(archiveFile).use { zipFile ->
            if (hostEntry != null && zipFile.getEntry(hostEntry) == null) {
                throw GradleException(
                    buildString {
                        appendLine("The plugin archive is missing the required host Kvist binary:")
                        appendLine(" - $hostEntry")
                        append("Archive: ${archiveFile.invariantSeparatorsPath}")
                    }
                )
            }

            optionalEntries
                .filter { zipFile.getEntry(it) == null }
                .forEach { missing ->
                    logger.warn(
                        "The plugin archive is missing optional Kvist binary: $missing. " +
                            "The plugin will be built without support for that platform."
                    )
                }
        }
    }
}