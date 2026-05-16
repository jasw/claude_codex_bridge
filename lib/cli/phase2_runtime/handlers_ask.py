from __future__ import annotations


def handle_ask(context, command, out, services) -> int:
    summary = services.submit_ask(context, command)
    services.write_lines(out, services.render_ask(summary))
    return 0


def handle_ask_wait(context, command, out, services) -> int:
    terminal = services.watch_ask_job(
        context,
        command.job_id,
        out,
        timeout=command.timeout_s,
        emit_output=True,
    )
    return services.exit_code_for_ask_status(terminal.status, reply=terminal.reply or '')


__all__ = ['handle_ask', 'handle_ask_wait']
