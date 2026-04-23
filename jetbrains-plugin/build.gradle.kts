import org.gradle.api.GradleException
import org.gradle.api.tasks.Sync

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

val validateDevAgentBinaries by tasks.registering {
    group = "verification"
    description = "Ensures that required DevAgent binaries exist before sandbox packaging tasks."

    doLast {
        val missingFiles = listOf(
            file("bin/win/devagent.exe"),
            file("bin/linux/devagent.bin"),
        ).filterNot { it.isFile }

        if (missingFiles.isNotEmpty()) {
            val message = buildString {
                appendLine("Missing required DevAgent binaries:")
                missingFiles.forEach { appendLine(" - ${it.invariantSeparatorsPath}") }
                append("Put the real binaries into jetbrains-plugin/bin before running packaging tasks.")
            }

            throw GradleException(message)
        }
    }
}

tasks.named<Sync>("prepareSandbox") {
    dependsOn(validateDevAgentBinaries)

    from("bin") {
        exclude("**/.gitkeep")
        into("${project.name}/bin")
    }
}

tasks.named("runIde") {
    dependsOn(validateDevAgentBinaries)
}

tasks.named("buildPlugin") {
    dependsOn(validateDevAgentBinaries)
}