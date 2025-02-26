""" 
Exports a run to a directory.
"""

import os
import json
import traceback
import mlflow
import click

from mlflow_export_import.common import filesystem as _filesystem
from mlflow_export_import.common.filesystem import mk_local_path
from mlflow_export_import.common.http_client import DatabricksHttpClient
from mlflow_export_import.common import MlflowExportImportException
from mlflow_export_import import utils, click_doc

print("MLflow Version:", mlflow.version.VERSION)
print("MLflow Tracking URI:", mlflow.get_tracking_uri())

class RunExporter():
    def __init__(self, mlflow_client=None, export_metadata_tags=False, notebook_formats=[]):
        """
        :param mlflow_client: MLflow client or if None create default client.
        :param export_metadata_tags: Export source run metadata tags.
        :param notebook_formats: List of notebook formats to export. Values are SOURCE, HTML, JUPYTER or DBC.
        """
        self.mlflow_client = mlflow_client or mlflow.tracking.MlflowClient()
        self.dbx_client = DatabricksHttpClient()
        print("Databricks REST client:",self.dbx_client)
        self.export_metadata_tags = export_metadata_tags
        self.notebook_formats = notebook_formats

    def get_metrics_with_steps(self, run):
        metrics_with_steps = {}
        for metric in run.data.metrics.keys():
            metric_history = self.mlflow_client.get_metric_history(run.info.run_id,metric)
            lst = [ utils.strip_underscores(m) for m in metric_history ]
            for x in lst:
                del x["key"] 
            metrics_with_steps[metric] = lst
        return metrics_with_steps

    def export_run(self, run_id, output_dir):
        """
        :param run_id: Run ID.
        :param output_dir: Output directory.
        :return: whether export succeeded.
        """
        fs = _filesystem.get_filesystem(output_dir)
        run = self.mlflow_client.get_run(run_id)
        fs.mkdirs(output_dir)
        tags =  utils.create_tags_for_metadata(self.mlflow_client, run, self.export_metadata_tags)
        dct = { "export_info": {
                    "mlflow_version": mlflow.__version__,
                    "mlflow_tracking_uri": mlflow.get_tracking_uri(),
                    "export_time": utils.get_now_nice() },
                "info": utils.strip_underscores(run.info) , 
                "params": run.data.params,
                "metrics": self.get_metrics_with_steps(run),
                "tags": tags,
              }
        path = os.path.join(output_dir,"run.json")
        utils.write_json_file(fs, path, dct)

        # copy artifacts
        dst_path = os.path.join(output_dir,"artifacts")
        try:
            TAG_NOTEBOOK_PATH = "mlflow.databricks.notebookPath"
            artifacts = self.mlflow_client.list_artifacts(run.info.run_id)
            if len(artifacts) > 0: # Because of https://github.com/mlflow/mlflow/issues/2839
                fs.mkdirs(dst_path)
                self.mlflow_client.download_artifacts(run.info.run_id,"", dst_path=mk_local_path(dst_path))
            notebook = tags.get(TAG_NOTEBOOK_PATH, None)
            if notebook is not None:
                if len(self.notebook_formats) > 0:
                    self.export_notebook(output_dir, notebook, run.data.tags, fs)
            elif len(self.notebook_formats) > 0:
                print(f"WARNING: Cannot export notebook since tag '{TAG_NOTEBOOK_PATH}' is not set.")
            return True
        except Exception as e:
            print("ERROR: run_id:",run.info.run_id,"Exception:",e)
            traceback.print_exc()
            return False

    def export_notebook(self, output_dir, notebook, tags, fs):
        notebook_dir = os.path.join(output_dir,"artifacts","notebooks")
        fs.mkdirs(notebook_dir)
        revision_id = tags["mlflow.databricks.notebookRevisionID"]
        notebook_path = tags["mlflow.databricks.notebookPath"]
        notebook_name = os.path.basename(notebook_path)
        manifest = { 
           "mlflow.databricks.notebookRevisionID": revision_id, 
           "mlflow.databricks.notebookPath": notebook_path,
           "mlflow.databricks.export-notebook-revision": revision_id }
        path = os.path.join(notebook_dir, "manifest.json")
        with open(path, "w") as f:
            f.write(json.dumps(manifest,indent=2)+"\n")
        for format in self.notebook_formats:
            self.export_notebook_format(notebook_dir, notebook, format, format.lower(), notebook_name, revision_id)

    def export_notebook_format(self, notebook_dir, notebook, format, extension, notebook_name, revision_id):
        params = { 
            "path": notebook, 
            "direct_download": True,
            "format": format,
            "revision_timestamp": revision_id 
        }
        try:
            rsp = self.dbx_client._get("workspace/export", params)
            notebook_path = os.path.join(notebook_dir, f"{notebook_name}.{extension}")
            utils.write_file(notebook_path, rsp.content)
        except MlflowExportImportException as e:
            print(f"WARNING: Cannot save notebook '{notebook}'. {e}")

@click.command()
@click.option("--run-id", 
    help="Run ID.", 
    type=str,
    required=True
)
@click.option("--output-dir", 
    help="Output directory.", 
    type=str,
    required=True
)
@click.option("--export-metadata-tags", 
    help=click_doc.export_metadata_tags, 
    type=bool, 
    default=False, 
    show_default=True
)
@click.option("--notebook-formats", 
    help=click_doc.notebook_formats, 
    type=str,
    default="", 
    show_default=True
)

def main(run_id, output_dir, export_metadata_tags, notebook_formats):
    print("Options:")
    for k,v in locals().items():
        print(f"  {k}: {v}")
    exporter = RunExporter(
      mlflow_client=None, 
      export_metadata_tags=export_metadata_tags, 
      notebook_formats=utils.string_to_list(notebook_formats))
    exporter.export_run(run_id, output_dir)

if __name__ == "__main__":
    main()
