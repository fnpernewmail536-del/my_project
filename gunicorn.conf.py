import os
bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
workers = 1
worker_class = "gthread"
threads = 4
timeout = 120
accesslog = "-"
errorlog = "-"
capture_output = True
preload_app = False
# Keep the master free of the optional control-server background thread.
control_socket_disable = True


def post_worker_init(worker):
    worker.log.info("Application startup complete; worker ready for HTTP")

