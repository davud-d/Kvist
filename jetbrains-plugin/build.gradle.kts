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

val validateHostKvistBinary by tasks.registering {
    group = "verification"
    description = "Ensures that the current host binary exists for local sandbox runs."
    notCompatibleWithConfigurationCache("Reads project-local binary files during task execution.")

    doLast {
        validateBinarySet(requiredBinariesForRunIde(), "the current host")
    }
}

val validateAllKvistBinaries by tasks.registering {
    group = "verification"
    description = "Ensures that all packaged Kvist binaries exist for plugin distribution builds."
    notCompatibleWithConfigurationCache("Reads project-local binary files during task execution.")

    doLast {
        validateBinarySet(requiredBinariesForBuild(), "plugin packaging")
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
    dependsOn(validateAllKvistBinaries)

    doLast {
        val archiveFile = archiveFile.get().asFile
        val expectedEntries = listOf(
            "$pluginDistributionDirectoryName/bin/win/kvist.exe",
            "$pluginDistributionDirectoryName/bin/linux/kvist.bin",
        )

        val missingEntries = ZipFile(archiveFile).use { zipFile ->
            expectedEntries.filter { expectedEntry -> zipFile.getEntry(expectedEntry) == null }
        }

        if (missingEntries.isNotEmpty()) {
            throw GradleException(
                buildString {
                    appendLine("The plugin archive is missing required Kvist binaries:")
                    missingEntries.forEach { appendLine(" - $it") }
                    append("Archive: ${archiveFile.invariantSeparatorsPath}")
                }
            )
        }
    }
}