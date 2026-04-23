import org.gradle.api.GradleException
import org.gradle.api.tasks.Sync
import org.gradle.api.tasks.bundling.AbstractArchiveTask
import java.io.File
import java.util.Locale

plugins {
    kotlin("jvm") version "2.2.21"
    id("org.jetbrains.intellij.platform") version "2.14.0"
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
        val message = buildString {
            appendLine("Missing required DevAgent binaries for $context:")
            missingFiles.forEach { appendLine(" - ${it.invariantSeparatorsPath}") }
            append("Put the real binaries into jetbrains-plugin/bin before running this task.")
        }

        throw GradleException(message)
    }
}

val devAgentSandboxBinaries = copySpec {
    from("bin") {
        exclude("**/.gitkeep", "linux/devagent.bin")
        into("${project.name}/bin")
    }

    from("bin") {
        include("linux/devagent.bin")
        into("${project.name}/bin")
        filePermissions {
            unix("rwxr-xr-x")
        }
    }
}

val validateHostDevAgentBinary by tasks.registering {
    group = "verification"
    description = "Ensures that the current host binary exists for local sandbox runs."

    doLast {
        validateBinarySet(requiredBinariesForRunIde(), "the current host")
    }
}

val validateAllDevAgentBinaries by tasks.registering {
    group = "verification"
    description = "Ensures that all packaged DevAgent binaries exist for plugin distribution builds."

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

tasks.named("buildPlugin") {
    dependsOn(validateAllDevAgentBinaries)
}

tasks.withType<AbstractArchiveTask>().configureEach {
    filesMatching("**/bin/linux/devagent.bin") {
        permissions {
            unix("rwxr-xr-x")
        }
    }
}