# Manual de servicio — Sierra huincha SH-900 (ejemplo ficticio)

Documento de demostración para probar Quasar. Los valores son ilustrativos y no corresponden a ningún equipo real.

## 1. Seguridad antes de intervenir

Antes de cualquier intervención aplique bloqueo y etiquetado (LOTO) en el seccionador principal Q1 y en la unidad hidráulica. Espere 5 minutos para la descarga de los condensadores del variador VF-1. Verifique ausencia de tensión con multímetro. Use guantes anticorte al manipular la huincha.

## 2. Códigos de error del controlador

### E-101 — Sobrecorriente motor principal
El variador VF-1 detectó una corriente superior al 150 % de la nominal durante más de 2 segundos. Causas típicas: avance de alimentación demasiado rápido para la densidad de la madera, huincha desafilada, rodamientos del volante trabados.
Procedimiento:
1. Reduzca la velocidad de avance del carro al 60 % y reinicie.
2. Inspeccione el filo de la huincha; si presenta dientes romos o fisuras, reemplácela.
3. Con la máquina bloqueada, gire el volante a mano: debe girar libre y sin ruido. Si hay resistencia, revise los rodamientos (sección 4).

### E-104 — Tensión de huincha fuera de rango
El sensor de tensión S4 marca una lectura fuera del rango 18–24 kN. Síntomas: la huincha "flamea", el corte sale ondulado o la máquina se detiene al arrancar.
Procedimiento:
1. Verifique la presión del cilindro tensor en el manómetro M2: debe estar entre 45 y 55 bar.
2. Si la presión es baja, revise fugas en el cilindro tensor y el nivel de aceite de la unidad hidráulica.
3. Si la presión es correcta pero el error persiste, limpie y recalibre el sensor S4 (sección 5).

### E-207 — Temperatura alta del aceite hidráulico
El aceite superó 65 °C. Causas: intercambiador de calor sucio, nivel bajo de aceite, válvula de alivio abierta permanentemente.
Procedimiento:
1. Detenga el equipo y deje enfriar.
2. Limpie las aletas del intercambiador con aire comprimido.
3. Verifique nivel de aceite (ISO VG 46) en el visor.
4. Si la unidad calienta rápidamente sin carga, revise la válvula de alivio V3.

### E-310 — Guía de huincha desalineada
El sensor láser L1 detecta desviación lateral mayor a 1,5 mm. Síntomas: corte cónico, huincha que se sale del volante, ruido de roce en las guías.
Procedimiento:
1. Revise el desgaste de los tacos de guía de cerámica; reemplace si el desgaste es mayor a 2 mm.
2. Ajuste la separación de guías a 0,3 mm de cada lado de la huincha.
3. Verifique la inclinación del volante superior (tracking) con la manilla de ajuste.

### E-402 — Parada de emergencia activa
Un pulsador de emergencia o la cortina de seguridad está activa. Revise los pulsadores SE1 a SE4 y el estado de la cortina óptica. Rearme desde el panel después de corregir la causa.

## 3. Fallas sin código

### La huincha se corta con frecuencia
Causas: tensión excesiva, volantes con diámetro gastado, soldadura de la huincha defectuosa, uso continuo sin descanso de la huincha. Deje reposar las huinchas 24 h entre turnos y verifique la tensión (E-104).

### Vibración excesiva del carro
Revise los rodamientos lineales del carro y la tensión de la cadena de arrastre. Una cadena floja produce golpeteo al invertir el sentido.

## 4. Rodamientos de volantes

Los volantes usan rodamientos 22216 E. Intervalo de lubricación: cada 250 horas con grasa EP2. Síntomas de falla: ruido metálico, temperatura del alojamiento superior a 80 °C, holgura axial perceptible.

## 5. Calibración del sensor de tensión S4

1. Libere completamente la tensión de la huincha.
2. En el panel, menú Servicio > Sensores > S4 > Cero.
3. Aplique tensión hasta 20 kN según el manómetro de referencia y confirme en Servicio > S4 > Span.
