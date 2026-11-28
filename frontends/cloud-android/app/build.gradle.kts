plugins {
    id("com.android.application")
}

// The cloud this build talks to. A release build says where the product lives; a test build points at a server
// on the computer that builds it: ./gradlew assembleDebug -PkvistCloudUrl=http://10.0.2.2:8742 (the emulator's host).
val cloudUrl = (findProperty("kvistCloudUrl") as String?) ?: "https://cloud.kvist.site"
// The Google Play build (-PkvistStore=true): the page sells nothing in it, as Play's payments policy asks of an app that
// does not use Play Billing. The APK from the GitHub release sells as the site does.
val storeBuild = (findProperty("kvistStore") as String?) == "true"

android {
    namespace = "site.kvist.cloud"
    compileSdk = 36

    defaultConfig {
        applicationId = "site.kvist.cloud"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "CLOUD_URL", "\"$cloudUrl\"")
        buildConfigField("boolean", "STORE", storeBuild.toString())
        // Plain HTTP only when the build points at a test server on this network; a product build refuses it.
        manifestPlaceholders["cleartext"] = cloudUrl.startsWith("http://").toString()
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.activity:activity:1.10.1")
    implementation("androidx.webkit:webkit:1.18.0")
    // The Custom Tab that signs in (GitHub's page will not run in a web view).
    implementation("androidx.browser:browser:1.8.0")
    // Google Pay: the operating system's own payment sheet (PaymentsClient).
    implementation("com.google.android.gms:play-services-wallet:20.0.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
