@echo off
title Java - gestion-configuracion-app - Logstash
echo Usuario del sistema:
whoami
echo Fecha y hora de inicio: %DATE% %TIME%
echo Aplicacion Java - puerto 8080
set "MAVEN_OPTS=-Djavax.net.ssl.trustStore=NONE -Djavax.net.ssl.trustStoreType=Windows-ROOT"
set "SPRING_PROFILES_ACTIVE=logstash"
set "JAVA_REPO=%~dp0..\..\ejemplo-grafana-elk"
pushd "%JAVA_REPO%"
call mvnw.cmd spring-boot:run
popd
echo La aplicacion Java se detuvo.
pause
