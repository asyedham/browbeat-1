from Connmon import Connmon
from Pbench import Pbench
from Tools import Tools
import os
import datetime
import glob
import logging
import shutil


class Rally:
    def __init__(self, config, hosts=None):
        self.logger = logging.getLogger('browbeat.Rally')
        self.config = config
        self.tools = Tools(self.config)
        self.connmon = Connmon(self.config)
        self.error_count = 0
        self.test_count = 0
        self.scenario_count = 0
        if hosts is not None:
            self.pbench = Pbench(self.config, hosts)

    def run_scenario(self, task_file, scenario_args, result_dir, test_name):
        self.logger.debug("--------------------------------")
        self.logger.debug("task_file: {}".format(task_file))
        self.logger.debug("scenario_args: {}".format(scenario_args))
        self.logger.debug("result_dir: {}".format(result_dir))
        self.logger.debug("test_name: {}".format(test_name))
        self.logger.debug("--------------------------------")

        if self.config['browbeat']['pbench']['enabled']:
            task_args = str(scenario_args).replace("'", "\\\"")
            self.pbench.register_tools()
            self.logger.info("Starting Scenario")
            tool = "rally"
            rally = self.tools.find_cmd(tool)
            cmd = ("user-benchmark --config={1} -- \"./pbench/browbeat-run-rally.sh"
                " {0} {1} \'{2}\'\"".format(task_file, test_name, task_args))
            self.tools.run_cmd(cmd)
        else:
            task_args = str(scenario_args).replace("'", "\"")
            cmd = "source {}; \\".format(self.config['browbeat']['rally_venv'])
            cmd +="rally task start {} --task-args \'{}\' 2>&1 | tee {}.log".format(task_file,
                task_args, test_name)
            self.tools.run_cmd(cmd)

    def workload_logger(self,result_dir) :
        base = result_dir.split('/')
        if not os.path.isfile("{}/{}/browbeat-rally-run.log".format(base[0],base[1])) :
            file = logging.FileHandler("{}/{}/browbeat-rally-run.log".format(base[0],base[1]))
            file.setLevel(logging.DEBUG)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)5s - %(message)s')
            file.setFormatter(formatter)
            self.logger.addHandler(file)
        return None

    def get_test_count(self):
        return self.test_count

    def get_error_count(self):
        return self.error_count

    def get_scenario_count(self):
        return self.scenario_count

    def get_task_id(self, test_name):
        cmd = "grep \"rally task results\" {}.log | awk '{{print $4}}'".format(test_name)
        return self.tools.run_cmd(cmd)

    def _get_details(self):
        self.logger.info("Current number of scenarios executed:{}".format(self.get_scenario_count()))
        self.logger.info("Current number of test(s) executed:{}".format(self.get_test_count()))
        self.logger.info("Current number of test failures:{}".format(self.get_error_count()))

    def gen_scenario_html(self, task_id, test_name):
        self.logger.info("Generating Rally HTML for task_id : {}".format(task_id))
        cmd = "rally task report {} --out {}.html".format(task_id, test_name)
        return self.tools.run_cmd(cmd)

    # Iterate through all the Rally scenarios to run.
    # If rerun is > 1, execute the test the desired number of times.
    def start_workloads(self):
        self.logger.info("Starting Rally workloads")
        time_stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.logger.debug("Time Stamp (Prefix): {}".format(time_stamp))
        benchmarks = self.config.get('rally')['benchmarks']
        if len(benchmarks) > 0:
            for benchmark in benchmarks:
                if benchmarks[benchmark]['enabled']:
                    self.logger.info("Benchmark: {}".format(benchmark))

                    scenarios = benchmarks[benchmark]['scenarios']
                    def_concurrencies = benchmarks[benchmark]['concurrency']
                    def_times = benchmarks[benchmark]['times']
                    self.logger.debug("Default Concurrencies: {}".format(def_concurrencies))
                    self.logger.debug("Default Times: {}".format(def_times))
                    for scenario in sorted(scenarios):
                        if scenarios[scenario]['enabled']:
                            self.scenario_count +=1
                            self.logger.info("Running Scenario: {}".format(scenario))
                            self.logger.debug("Scenario File: {}".format(
                                scenarios[scenario]['file']))

                            scenario_args = dict(scenarios[scenario])
                            del scenario_args['enabled']
                            del scenario_args['file']
                            if len(scenario_args) > 0:
                                self.logger.debug("Overriding Scenario Args: {}".format(
                                    scenario_args))

                            result_dir = self.tools.create_results_dir(
                                self.config['browbeat']['results'], time_stamp, benchmark,
                                scenario)
                            self.logger.debug("Created result directory: {}".format(result_dir))
                            self.workload_logger(result_dir)

                            # Override concurrency/times
                            if 'concurrency' in scenario_args:
                                concurrencies = scenario_args['concurrency']
                                del scenario_args['concurrency']
                            else:
                                concurrencies = def_concurrencies
                            if 'times' not in scenario_args:
                                scenario_args['times'] = def_times

                            for concurrency in concurrencies:
                                scenario_args['concurrency'] = concurrency
                                for run in range(self.config['browbeat']['rerun']):
                                    self.test_count+=1
                                    test_name = "{}-browbeat-{}-{}-iteration-{}".format(time_stamp,
                                        scenario, concurrency, run)

                                    if not result_dir:
                                        self.logger.error("Failed to create result directory")
                                        exit(1)

                                    # Start connmon before rally
                                    if self.config['browbeat']['connmon']:
                                        self.connmon.start_connmon()

                                    self.run_scenario(scenarios[scenario]['file'], scenario_args,
                                        result_dir, test_name)

                                    # Stop connmon at end of rally task
                                    if self.config['browbeat']['connmon']:
                                        self.connmon.stop_connmon()
                                        try :
                                            self.connmon.move_connmon_results(result_dir, test_name)
                                        except:
                                            self.logger.error("Connmon Result data missing, Connmon never started")
                                            return False
                                        self.connmon.connmon_graphs(result_dir, test_name)

                                    # Find task id (if task succeeded in running)
                                    task_id = self.get_task_id(test_name)
                                    if task_id:
                                        self.gen_scenario_html(task_id, test_name)
                                    else:
                                        self.logger.error("Cannot find task_id")
                                        self.error_count +=1

                                    for data in glob.glob("./{}*".format(test_name)):
                                        shutil.move(data, result_dir)

                                    if self.config['browbeat']['pbench']['enabled']:
                                        pbench_results_dir = self.pbench.get_results_dir(time_stamp)
                                        shutil.copytree(result_dir,
                                            "{}/results/".format(pbench_results_dir))
                                        self.pbench.move_results()
                                    self._get_details()

                        else:
                            self.logger.info("Skipping {} scenario enabled: false".format(scenario))
                else:
                    self.logger.info("Skipping {} benchmarks enabled: false".format(benchmark))
        else:
            self.logger.error("Config file contains no rally benchmarks.")
