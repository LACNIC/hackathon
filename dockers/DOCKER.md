# Hackathon local

Hackathon es un sitio HTML estático publicado en GitHub Pages. Docker sólo
sirve una copia local de sus páginas y assets; no ejecuta Java, Maven, Python
histórico ni una base de datos. CNAME y el contenido del sitio se conservan.

```bash
./dockers/docker-local.sh validate
./dockers/docker-local.sh up
./dockers/docker-local.sh check
./dockers/docker-local.sh logs
./dockers/docker-local.sh down
./dockers/docker-local.sh reset
```

URL: http://127.0.0.1:8107/. La portada redirige a la edición 2026, que conserva
español, inglés y portugués. Los enlaces a plataformas, sitios y fuentes de
terceros mantienen sus destinos existentes; no son servicios Docker locales.

Doctor descarga la base declarada `nginx:1.28-alpine`. El aprovisionamiento
manual equivalente es `docker pull nginx:1.28-alpine`. `up` no hace pulls ni
pushes implícitos. Construye la imagen desde un contexto curado, no desde todo
el checkout. Si la base falta, falla con una indicación explícita.

Se incluyen sólo archivos versionados de `index.html` y de las carpetas
públicas 2017, 2019, 2024, 2025, 2026 y `17 MVD`, con extensiones de página,
estilos, scripts de navegador, imágenes, fuentes y documentos públicos. Los
archivos nuevos deben agregarse al índice Git para incorporarlos. Se rechazan
symlinks; dotfiles, archivos internos, scripts Python/shell y archivos no
versionados quedan fuera del contexto. Docker recibe sólo esos bytes y los
dos archivos de infraestructura necesarios para construir la imagen.

Nginx corre como usuario sin privilegios, con filesystem de sólo lectura, sin
capabilities y con 64 MiB de memoria. El único puerto publicado es loopback
8107. No monta el checkout ni crea volúmenes de datos. No hay listing de
carpetas; las rutas privadas y ausentes devuelven 404. `check` compara los
bytes HTTP con páginas/assets originales e incluye negativos de privacidad.

El wrapper comprueba ownership por nombre y etiquetas Compose antes de operar
contenedores/red. Ante un puerto ocupado falla; nunca busca otro. El
fingerprint cubre el sitio y la configuración: un cambio fuerza `fresh`,
reconstrucción y readiness. Un digest adicional de nombres y bytes públicos,
etiquetado en la imagen, detecta también `git add` o `git rm --cached` sin
cambios en los bytes del checkout. `reuse` conserva el contenedor y vuelve a validar
HTTP. `down` elimina sólo recursos propios; `reset` rehace el servicio desde
el contenido canónico, sin datos persistentes que restaurar.

`validate` funciona sin Docker, red ni credenciales. El recibo local ignorado
se escribe ready sólo después de verificar páginas, idiomas, assets y 404.
GitHub Pages continúa publicándose por su configuración existente: este
runtime no despliega ni modifica hosting.
