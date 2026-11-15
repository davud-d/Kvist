plugins {
    id("com.android.application")
}

android {
    namespace = "site.kvist.android"
    compileSdk = 36

    defaultConfig {
        applicationId = "site.kvist.android"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addStaticSourceDirectory(layout.buildDirectory.dir("generated/web-assets").get().asFile.absolutePath)
    }
}

// The connect screen shares its look with the page the computer serves: the same stylesheet, sprites,
// fonts and small modules, taken from the web client instead of copied, so the two cannot drift apart.
val webClient = layout.projectDirectory.dir("../../web-local/kvist_web/client")
val copyWebClient by tasks.registering(Sync::class) {
    from(webClient) {
        include(
            "mobile/pixel.css", "mobile/season.js", "mobile/i18n.js", "mobile/mascot.js", "mobile/snow.js",
            "mobile/sprites/**", "mobile/fonts/**", "fonts/JetBrainsMono.woff2", "kvist-mark.svg",
        )
    }
    into(layout.buildDirectory.dir("generated/web-assets"))
}
// Every task that reads assets (merge, lint) waits for the copy.
tasks.matching { it.name.endsWith("Assets") || it.name.startsWith("lint") }.configureEach { dependsOn(copyWebClient) }

dependencies {
    implementation("androidx.activity:activity:1.10.1")
    implementation("androidx.webkit:webkit:1.18.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
