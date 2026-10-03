var NodeHelper = require("node_helper");
const spawn   = require("child_process").spawn;
const exec    = require("child_process").exec;
const path    = require("path");
const fs      = require("fs");

module.exports = NodeHelper.create({

    init() {},
    start() {},
    stop() {},

    socketNotificationReceived(notification, payload) {
        if (notification === "DO_PYTHON") {
            this.api(payload);
        } else {
            console.log(this.name + " received a socket notification: " + notification + " - Payload: " + payload);
        }
    },

    api(payload) {
        const pic_folder = __dirname + path.sep + "Pictures" + path.sep + payload.id;
        if (!fs.existsSync(pic_folder)) {
            fs.mkdirSync(pic_folder, { recursive: true });
        }

        // Check Python version
        exec(payload.config.python + " --version", (error, stdout, stderr) => {
            // Python 3 prints version to stderr on older versions, stdout on newer
            const versionOutput = stdout || stderr;
            const match = versionOutput.match(/(\d+)\.(\d+)/);
            if (!match) {
                this.sendSocketNotification("PYTHON_DONE", {
                    id: payload.id,
                    data: "{'status': 0, 'error': 'Could not determine Python version'}"
                });
                return;
            }

            const pyMajor = parseInt(match[1]);
            const pyMinor = parseInt(match[2]);

            if (pyMajor >= 3 && pyMinor >= 9) {
                // Pass MQTT topic (username field) as first argument to api.py
                const mqttTopic = payload.config.username || "vwdata/mycar";
                const handler = spawn(payload.config.python, [
                    "-u",
                    __dirname + path.sep + "api.py",
                    mqttTopic,
                    pic_folder
                ]);

                handler.stdout.on("data", (data) => {
                    console.log("MMM-weconnectid_MQTT: got data for topic " + mqttTopic);
                    this.sendSocketNotification("PYTHON_DONE", {
                        id: payload.id,
                        data: data.toString(),
                        pics: pic_folder
                    });
                });

                handler.stderr.on("data", (data) => {
                    console.error("MMM-weconnectid_MQTT stderr: " + data.toString());
                });

            } else {
                this.sendSocketNotification("PYTHON_DONE", {
                    id: payload.id,
                    data: "{'status': 0, 'error': 'Python 3.9 or higher required. Current: " + pyMajor + "." + pyMinor + "'}"
                });
            }
        });
    }
});
