# custom-project-agent

Esta carpeta contiene reglas, decisiones y contexto especificos del proyecto
destino. No forma parte del framework reusable del arnes.

Usala para documentar:

- arquitectura local;
- comandos de testing locales;
- runtime local, Docker, WildFly, deploy y login de testing;
- datos, migraciones o convenciones de base;
- integraciones, jobs o runbooks operativos;
- excepciones justificadas a las reglas generales del arnes.

Los agentes leen solo los archivos cuyo alcance afecte la tarea, despues de
`ai-harness/AGENTS.md` y antes de tocar ese codigo.

Si un archivo no está listo para usar, nómbralo con `draft` en el nombre.
Ese contenido se interpreta como ejemplo/placeholder y no se usa para decisiones
operativas ni para el análisis automático.

Para validacion UI local, completa `runtime-testing.md` y usa
`ai-harness-local/testing/.env` para credenciales o valores sensibles.
