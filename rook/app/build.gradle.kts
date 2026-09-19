plugins {
    id("com.android.application")
}

android {
    namespace = "com.echoportal"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.echoportal"
        minSdk = 30          // LineageOS 18.1 = Android 11
        targetSdk = 30
        versionCode = 1
        versionName = "0.1"
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:5.5.0")
}
