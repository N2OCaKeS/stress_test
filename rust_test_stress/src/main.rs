use libc;
use std::io::prelude::*;
use std::fs;
use std::fs::OpenOptions;
use std::os::unix::fs::OpenOptionsExt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};
use std::thread;
use std::time::Duration;
use std::path::PathBuf;
use std::process::exit;
use clap::{Parser};
use rand::{distributions::Alphanumeric, Rng};


struct Count {
    inb: u64,
    outb: u64,
}

#[derive(Parser)]
#[command(version, arg_required_else_help(true))]
/// Writes multiple files with threads
struct Cli {
    /// Path to operate in
    path: PathBuf,

    /// Test file length
    #[arg(default_value_t = 1, short, long)]
    length: usize,

    /// Test duration in seconds
    #[arg(default_value_t = 10, short = 't', long)]
    duration: u64,

    /// Test workers number
    #[arg(default_value_t = 8, short, long)]
    workers: u32,
}

fn main() {
    let cli = Cli::parse();
    let path = cli.path;
    let length = cli.length;
    let duration = cli.duration;
    let number = cli.workers;

    if let Err(why) = fs::create_dir_all(&path) {
        println!("couldn't create {}: {}", path.display(), why);
        exit(1);
    };

    let (tx, rx) = mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let base = Arc::new(path);

    for _ in 0..number {
        let tx = tx.clone();
        let stop = stop.clone();
        let length = length;
        let base = base.clone();

        thread::spawn(move || {
            let mut sum = Count { inb: 0, outb: 0 };
            let out_buf: Vec<u8> = vec![0; length];
            
            loop {
                let filename: String = rand::thread_rng()
                    .sample_iter(&Alphanumeric)
                    .take(7)
                    .map(char::from)
                    .collect();
                let path = (*base).join(&filename);
                let display = path.display();
                
                let mut options = OpenOptions::new();
                options.create(true).write(true).custom_flags(libc::O_SYNC);
                let mut file = match options.open(&path) {
                    Err(why) => {
                        println!("couldn't create {}: {}", display, why);
                        break;
                    },
                    Ok(file) => file,
                };
                if let Err(why) = file.write_all(&out_buf) {
                    println!("couldn't write to {}: {}", display, why);
                }
                let _ = fs::remove_file(&path);

                sum.outb += 1;
                if (*stop).load(Ordering::Relaxed) {
                    break;
                }
            }
            tx.send(sum).unwrap();
        });
    }

    thread::sleep(Duration::from_secs(duration));
    (*stop).store(true, Ordering::Relaxed);

    let mut sum = Count { inb: 0, outb: 0 };
    for _ in 0..number {
        let c: Count = rx.recv().unwrap();
        sum.inb += c.inb;
        sum.outb += c.outb;
    }
    println!("Benchmarking: {}", (*base).display());
    println!(
        "{} clients, running {} bytes, {} sec.",
        number, length, duration
    );
    println!();
    println!(
        "Speed: {} ops/sec",
        sum.outb / duration,
    );
}
