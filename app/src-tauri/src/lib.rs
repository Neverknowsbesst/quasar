//! Shell de escritorio: abre la ventana y levanta el motor Python como proceso hijo.

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

const PUERTO: &str = "8765";

struct ProcesoMotor(Mutex<Option<Child>>);

/// Carpeta que contiene el paquete `quasar_motor`.
fn directorio_motor(app: &tauri::AppHandle) -> Option<PathBuf> {
    let candidatos = [
        std::env::var_os("QUASAR_DIR_MOTOR").map(PathBuf::from),
        // App empaquetada: el motor viaja como recurso.
        app.path().resource_dir().ok().map(|d| d.join("motor")),
        // Desarrollo: app/src-tauri/../../motor
        Some(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../motor")),
    ];
    candidatos
        .into_iter()
        .flatten()
        .find(|dir| dir.join("quasar_motor").join("__main__.py").is_file())
}

fn interprete_python() -> String {
    std::env::var("QUASAR_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) { "python" } else { "python3" }.to_string()
    })
}

fn lanzar_motor(app: &tauri::AppHandle) -> Option<Child> {
    let Some(dir) = directorio_motor(app) else {
        eprintln!("[quasar] no se encontró la carpeta del motor; define QUASAR_DIR_MOTOR");
        return None;
    };
    let mut comando = Command::new(interprete_python());
    comando
        .args(["-m", "quasar_motor", "--puerto", PUERTO, "--vigilar-padre"])
        .current_dir(&dir);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const SIN_VENTANA: u32 = 0x0800_0000; // CREATE_NO_WINDOW
        comando.creation_flags(SIN_VENTANA);
    }
    match comando.spawn() {
        Ok(hijo) => {
            println!("[quasar] motor iniciado desde {}", dir.display());
            Some(hijo)
        }
        Err(err) => {
            eprintln!("[quasar] no se pudo iniciar Python ({err}); define QUASAR_PYTHON");
            None
        }
    }
}

pub fn ejecutar() {
    tauri::Builder::default()
        .setup(|app| {
            let hijo = lanzar_motor(app.handle());
            app.manage(ProcesoMotor(Mutex::new(hijo)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error al construir la aplicación")
        .run(|app, evento| {
            if let RunEvent::Exit = evento {
                if let Some(mut hijo) = app.state::<ProcesoMotor>().0.lock().unwrap().take() {
                    let _ = hijo.kill();
                }
            }
        });
}
