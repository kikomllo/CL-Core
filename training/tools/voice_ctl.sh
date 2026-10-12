#!/bin/bash
# Controller for the local voice-training jobs that run inside WSL (see voice_job.py, the Windows-side wrapper).
#
#   voice_ctl.sh status                  every registered job: running / paused / dead, plus GPU use
#   voice_ctl.sh pause [name]            freeze the job's whole process tree (SIGSTOP). CPU and disk stop at once,
#                                        BUT a paused CUDA job still holds its GPU memory -> use `stop` before gaming
#   voice_ctl.sh resume [name]           unfreeze (SIGCONT)
#   voice_ctl.sh stop [name]             end the job (SIGTERM, then SIGKILL); training restarts from its last checkpoint
#   voice_ctl.sh start <name> <cmd...>   run a command as a registered job (own session, log in ~/voice/<name>.log)
#
# A job is a PID file ~/voice/jobs/<name>.pid holding its root process; the tree below it is found at call time.
JOBS="$HOME/voice/jobs"
mkdir -p "$JOBS"

descend() { for c in $(pgrep -P "$1" 2>/dev/null); do echo "$c"; descend "$c"; done; }
names()   { for f in "$JOBS"/*.pid; do [ -e "$f" ] && basename "$f" .pid; done; }
root_of() { cat "$JOBS/$1.pid" 2>/dev/null; }
alive()   { local r; r=$(root_of "$1"); [ -n "$r" ] && kill -0 "$r" 2>/dev/null; }
tree_of() { alive "$1" || return 0; local r; r=$(root_of "$1"); echo "$r"; descend "$r"; }
state_of() {
  if ! alive "$1"; then echo dead; return; fi
  local s; s=$(ps -o stat= -p "$(root_of "$1")" | cut -c1)
  if [ "$s" = "T" ]; then echo paused; else echo running; fi
}
targets() { if [ -n "$1" ]; then echo "$1"; else names; fi; }

case "$1" in
  status)
    any=0
    for n in $(names); do
      any=1
      r=$(root_of "$n")
      if alive "$n"; then
        procs=$(tree_of "$n" | wc -l)
        mem=$(ps -o rss= -p $(tree_of "$n" | tr '\n' ',' | sed 's/,$//') 2>/dev/null | awk '{s+=$1} END {printf "%.0f", s/1024}')
        printf "%-12s %-8s pid %-7s up %-10s %2d processes, %s MB RAM\n" "$n" "$(state_of "$n")" "$r" "$(ps -o etime= -p "$r" | tr -d ' ')" "$procs" "$mem"
      else
        printf "%-12s %-8s\n" "$n" dead
      fi
    done
    [ "$any" = 0 ] && echo "no jobs registered"
    echo "--- GPU (whole machine): $(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null)"
    echo "--- WSL memory: $(free -m | awk 'NR==2 {print $3 " MB used of " $2 " MB"}')"
    ;;
  pause)
    for n in $(targets "$2"); do
      pids=$(tree_of "$n"); [ -z "$pids" ] && { echo "$n: not running"; continue; }
      for p in $pids; do kill -STOP "$p" 2>/dev/null; done
      echo "$n: paused ($(echo "$pids" | wc -l) processes)"
    done ;;
  resume)
    for n in $(targets "$2"); do
      pids=$(tree_of "$n"); [ -z "$pids" ] && { echo "$n: not running"; continue; }
      for p in $pids; do kill -CONT "$p" 2>/dev/null; done
      echo "$n: resumed"
    done ;;
  stop)
    for n in $(targets "$2"); do
      pids=$(tree_of "$n"); [ -z "$pids" ] && { echo "$n: not running"; rm -f "$JOBS/$n.pid"; continue; }
      for p in $pids; do kill -CONT "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null; done
      sleep 4
      for p in $pids; do kill -0 "$p" 2>/dev/null && kill -KILL "$p" 2>/dev/null; done
      rm -f "$JOBS/$n.pid"
      echo "$n: stopped"
    done ;;
  start)
    name="$2"; shift 2
    [ -z "$name" ] || [ $# -eq 0 ] && { echo "usage: start <name> <command...>"; exit 2; }
    alive "$name" && { echo "$name: already running"; exit 1; }
    nohup setsid bash -c 'echo $$ > "$0"; exec "$@"' "$JOBS/$name.pid" "$@" > "$HOME/voice/$name.log" 2>&1 &
    sleep 1
    echo "$name: started (pid $(root_of "$name")), log ~/voice/$name.log" ;;
  *)
    echo "usage: voice_ctl.sh status | pause [name] | resume [name] | stop [name] | start <name> <cmd...>"; exit 2 ;;
esac
