# Runtime y testing local

Este proyecto es HTML estático para GitHub Pages. No usar Java, Maven, WildFly
ni bases de datos; los scripts Python históricos son material archivado.

## Entrada canónica

```bash
./dockers/docker-local.sh validate
./dockers/docker-local.sh up
./dockers/docker-local.sh check
./dockers/docker-local.sh logs
./dockers/docker-local.sh down
./dockers/docker-local.sh reset
```

URL http://127.0.0.1:8107/. Probar portada, idiomas 2026, ediciones anteriores,
assets CSS/imágenes/PDF y rutas públicas con espacios. `check` verifica su
contenido HTTP y rechaza exposición de archivos internos y directory listing.

No hay login, credenciales ni seeds. Los servicios enlazados en las páginas
siguen siendo externos; no simularlos ni ejecutar sus acciones para verificar
este servidor estático. Preservar CNAME y el despliegue GitHub Pages.

La topología y la provisión están en `dockers/local-runtime.json`; los detalles
de aislamiento y de archivos públicos permitidos están en `dockers/DOCKER.md`.
