@echo off
setlocal

set "DIRNAME=%~dp0"
if "%DIRNAME%"=="" set "DIRNAME=."
for %%i in ("%DIRNAME%") do set "APP_HOME=%%~fi"
set "CLASSPATH=%APP_HOME%\gradle\wrapper\gradle-wrapper.jar"

if defined JAVA_HOME (
    set "JAVA_EXE=%JAVA_HOME%\bin\java.exe"
) else (
    set "JAVA_EXE=java.exe"
    where "%JAVA_EXE%" >NUL 2>&1
    if errorlevel 1 (
        echo ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH. 1>&2
        exit /b 1
    )
)

if defined JAVA_HOME if not exist "%JAVA_EXE%" (
    echo ERROR: JAVA_HOME is set to an invalid directory: %JAVA_HOME% 1>&2
    exit /b 1
)

"%JAVA_EXE%" ^
  -Dfile.encoding=UTF-8 ^
  -Xmx64m ^Й
  -Xms64m ^
  -Dorg.gradle.appname=gradlew ^
  -classpath "%CLASSPATH%" ^
  org.gradle.wrapper.GradleWrapperMain ^
  %*

exit /b %ERRORLEVEL%