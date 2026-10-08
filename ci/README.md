# Jobs Jenkins de hackathon

Este README se genera desde `ai-harness/harness/consumer-ci/README.md` al
sincronizar `ci/`. La configuración activa de Jenkins se administra en Jenkins;
versionar estos archivos no cambia los jobs.

## Estado de este checkout

Colección Newman: no hay `postman/collection.json` registrado; no configurar un job Newman aún.

Deploy nuevo con Kuma: este checkout no tiene el job Docker legado y sus archivos de build/Compose completos.

## Job de pruebas Newman

Este proyecto no tiene pruebas Newman registradas. La carpeta `ci/newman/` está instalada pero inactiva.

## Job de deploy (Kuma solo en producción cuando aplica)

No hay una entrada de deploy Docker lista para migrar en este checkout. `ci/deploy/` y `ci/kuma/` quedan inactivos.

## Verificación antes de versionar

Desde la raíz del checkout, con el enlace local `ai-harness` disponible:

```sh
python3 -B ai-harness/harness/framework/sync-consumer-ci.py --project-root . --check
```

En Jenkins alcanza el checkout de este proyecto: los wrappers verifican
`ci/manifest.json` antes de ejecutar. Si hay un job Newman, publicá los XML
JUnit indicados arriba para que muestre sus resultados.
