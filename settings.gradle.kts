plugins {
    // 로컬에 JDK 17이 없어도 toolchain이 자동 다운로드되도록 한다 (Windows/맥 공통)
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
}

rootProject.name = "aptprice"
