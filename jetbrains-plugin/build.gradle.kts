import org.gradle.api.GradleException
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
    }
}

kotlin {
    jvmToolchain(21)
}

val hostOsName = System.getProperty("os.name").lowercase(Locale.US)
val pluginDistributionDirectoryName = project.name

fun requiredBinariesForBuild(): List<File> = listOf(
    file("bin/win/devagent.exe"),
    file("bin/linux/devagent.bin"),
)

fun requiredBinariesForRunIde(): List<File> {
    return when {
        hostOsName.contains("win") -> listOf(file("bin/win/devagent.exe"))
        hostOsName.contains("linux") -> listOf(file("bin/linux/devagent.bin"))
        else -> emptyList()
    }
}

fun validateBinarySet(files: List<File>, context: String) {
    val missingFiles = files.filterNot { it.isFile }

    if (missingFiles.isNotEmpty()) {
        throw GradleException(
            buildString {
                appendLine("Missing required DevAgent binaries for $context:")
                missingFiles.forEach { appendLine(" - ${it.invariantSeparatorsPath}") }
                appendLine("Expected files:")
                appendLine(" - bin/win/devagent.exe")
                append(" - bin/linux/devagent.bin")
            }
        )
    }
}

val devAgentSandboxBinaries = copySpec {
    from("bin/win") {
        exclude("**/.gitkeep")
        into("$pluginDistributionDirectoryName/bin/win")
        includeEmptyDirs = false
    }

    from("bin/linux") {
        include("devagent.bin")
        into("$pluginDistributionDirectoryName/bin/linux")
        includeEmptyDirs = false
        filePermissions {
            unix("rwxr-xr-x")
        }
    }
}

val validateHostDevAgentBinary by tasks.registering {
    group = "verification"
    description = "Ensures that the current host binary exists for local sandbox runs."
    notCompatibleWithConfigurationCache("Reads project-local binary files during task execution.")

    doLast {
        validateBinarySet(requiredBinariesForRunIde(), "the current host")
    }
}

val validateAllDevAgentBinaries by tasks.registering {
    group = "verification"
    description = "Ensures that all packaged DevAgent binaries exist for plugin distribution builds."
    notCompatibleWithConfigurationCache("Reads project-local binary files during task execution.")

    doLast {
        validateBinarySet(requiredBinariesForBuild(), "plugin packaging")
    }
}

tasks.named<Sync>("prepareSandbox") {
    dependsOn(validateHostDevAgentBinary)
    with(devAgentSandboxBinaries)
}

tasks.named("runIde") {
    dependsOn(validateHostDevAgentBinary)
}

tasks.named<Zip>("buildPlugin") {
    dependsOn(validateAllDevAgentBinaries)

    doLast {
        val archiveFile = archiveFile.get().asFile
        val expectedEntries = listOf(
            "$pluginDistributionDirectoryName/bin/win/devagent.exe",
            "$pluginDistributionDirectoryName/bin/linux/devagent.bin",
        )

        val missingEntries = ZipFile(archiveFile).use { zipFile ->
            expectedEntries.filter { expectedEntry -> zipFile.getEntry(expectedEntry) == null }
        }

        if (missingEntries.isNotEmpty()) {
            throw GradleException(
                buildString {
                    appendLine("The plugin archive is missing required DevAgent binaries:")
                    missingEntries.forEach { appendLine(" - $it") }
                    append("Archive: ${archiveFile.invariantSeparatorsPath}")
                }
            )
        }
    }
}