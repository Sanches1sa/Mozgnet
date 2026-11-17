// mozgnet — мобильное приложение «Мозг-12»: облако на Python в телефоне (Chaquopy),
// скан локальной сети, платы и облака в одном окне.
plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

val runNumber = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
val repoSlug = System.getenv("GITHUB_REPOSITORY") ?: ""
// v1.1: один репозиторий — облако (cloud/), плата (board/), лаунчер (launcher/)
// и приложение. Код облака попадает в APK из cloud/ при каждой сборке.
val cloudVer = rootProject.file("cloud/VERSION").takeIf { it.exists() }?.readText()?.trim() ?: "?"
val genAssets = layout.buildDirectory.dir("generated/mozgAssets")
val copyMozgAssets = tasks.register<Sync>("copyMozgAssets") {
    into(genAssets)
    from(rootProject.file("cloud")) {
        into("cloud")
        include("server/**", "web/**", "docs/**", "VERSION", "README.md", "AGENT-GUIDE.md")
        exclude("**/__pycache__/**", "**/*.pyc", "data/**")
        includeEmptyDirs = false
    }
    from(rootProject.file("launcher/app/n12_mac.sh")) { into("board") }
}
val withNumpy = (project.findProperty("mozgnet.numpy") ?: "true").toString() == "true"

android {
    namespace = "ru.mozgnet.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.mozgnet.app"
        minSdk = 26          // v1.1: SSH-клиент (JSch) — Android 8+
        targetSdk = 35
        versionCode = runNumber
        versionName = "1.0.$runNumber (облако $cloudVer)"
        buildConfigField("String", "REPO", "\"$repoSlug\"")
        buildConfigField("String", "CLOUD_VERSION", "\"$cloudVer\"")
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")   // Python 3.12 в Chaquopy — только 64-битные
        }
    }

    // Один ключ на все сборки — чтобы новое APK ставилось ПОВЕРХ старого.
    // Это отладочный ключ (как у Android Studio), он не секретный.
    signingConfigs {
        create("mozgnet") {
            storeFile = file("mozgnet.keystore")
            storePassword = "android"
            keyAlias = "mozgnet"
            keyPassword = "android"
        }
    }
    buildTypes {
        getByName("debug") { signingConfig = signingConfigs.getByName("mozgnet") }
        getByName("release") {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("mozgnet")
        }
    }
    buildFeatures { buildConfig = true }
    sourceSets {
        getByName("main") { assets.srcDir(genAssets) }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    }
    androidResources {
        // облако хранит файлы с точкой и подчёркиванием — не отбрасывать
        ignoreAssetsPattern = "!.svn:!.git:!.ds_store:!*.scc:!CVS:!thumbs.db:!picasa.ini:!*~:!__pycache__"
    }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            if (withNumpy) install("numpy")
        }
    }
}

tasks.named("preBuild") { dependsOn(copyMozgAssets) }
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach {
    dependsOn(copyMozgAssets)
}

dependencies {
    // SSH до платы — вращение MAC с телефона (как в лаунчере)
    implementation("com.github.mwiede:jsch:0.2.20")
}
