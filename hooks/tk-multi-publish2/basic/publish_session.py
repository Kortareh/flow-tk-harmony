# Copyright (c) 2017 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

import os
import glob
import shutil
import fnmatch
import traceback
import sys

import sgtk
from sgtk.util.filesystem import ensure_folder_exists

try:
    _unicode_type = unicode
except NameError:
    _unicode_type = ()


__author__ = "Diego Garcia Huerta"
__contact__ = "https://www.linkedin.com/in/diegogh/"


HookBaseClass = sgtk.get_hook_baseclass()


class HarmonySessionPublishPlugin(HookBaseClass):
    """
    Plugin for publishing an open Harmony session.

    This hook relies on functionality found in the base file publisher hook in
    the publish2 app and should inherit from it in the configuration. The hook
    setting for this plugin should look something like this::

        hook: "{self}/publish_file.py:{engine}/tk-multi-publish2/basic/publish_session.py"

    """

    # NOTE: The plugin icon and name are defined by the base file plugin.

    @property
    def description(self):
        """
        Verbose, multi-line description of what the plugin does. This can
        contain simple html for formatting.
        """

        loader_url = "https://support.shotgunsoftware.com/hc/en-us/articles/219033078"

        return """
        Publishes the file to Shotgun. A <b>Publish</b> entry will be
        created in Shotgun which will include a reference to the file's current
        path on disk. If a publish template is configured, a copy of the
        current session will be copied to the publish template path which
        will be the file that is published. Other users will be able to access
        the published file via the <b><a href='%s'>Loader</a></b> so long as
        they have access to the file's location on disk.

        If the session has not been saved, validation will fail and a button
        will be provided in the logging output to save the file.

        <h3>File versioning</h3>
        If the filename contains a version number, the process will bump the
        file to the next version after publishing.

        The <code>version</code> field of the resulting <b>Publish</b> in
        Shotgun will also reflect the version number identified in the filename
        The basic worklfow recognizes the following version formats by default:

        <ul>
        <li><code>filename.v###.ext</code></li>
        <li><code>filename_v###.ext</code></li>
        <li><code>filename-v###.ext</code></li>
        </ul>

        After publishing, if a version number is detected in the work file, the
        work file will automatically be saved to the next incremental version
        number. For example, <code>filename.v001.ext</code> will be published
        and copied to <code>filename.v002.ext</code>

        If the next incremental version of the file already exists on disk, the
        validation step will produce a warning, and a button will be provided
        in the logging output which will allow saving the session to the next
        available version number prior to publishing.

        <br><br><i>NOTE: any amount of version number padding is supported. for
        non-template based workflows.</i>

        <h3>Overwriting an existing publish</h3>
        In non-template workflows, a file can be published multiple times,
        however only the most recent publish will be available to other users.
        Warnings will be provided during validation if there are previous
        publishes.
        """ % (
            loader_url,
        )

    @property
    def settings(self):
        """
        Dictionary defining the settings that this plugin expects to receive
        through the settings parameter in the accept, validate, publish and
        finalize methods.

        A dictionary on the following form::

            {
                "Settings Name": {
                    "type": "settings_type",
                    "default": "default_value",
                    "description": "One line description of the setting"
            }

        The type string should be one of the data types that toolkit accepts as
        part of its environment configuration.
        """

        # inherit the settings from the base publish plugin
        base_settings = super(HarmonySessionPublishPlugin, self).settings or {}

        base_settings["File Types"]["default"].append(["Harmony Project File", "xstage"])

        # settings specific to this class
        harmony_publish_settings = {
            "Publish Template": {
                "type": "template",
                "default": None,
                "description": "Template path for published work files. Should"
                "correspond to a template defined in "
                "templates.yml.",
            }
        }

        # update the base settings
        base_settings.update(harmony_publish_settings)

        return base_settings

    @property
    def item_filters(self):
        """
        List of item types that this plugin is interested in.

        Only items matching entries in this list will be presented to the
        accept() method. Strings can contain glob patters such as *, for 
        example ["harmony.*", "file.harmony"]
        """
        return ["harmony.session"]

    def accept(self, settings, item):
        """
        Method called by the publisher to determine if an item is of any
        interest to this plugin. Only items matching the filters defined via 
        the item_filters property will be presented to this method.

        A publish task will be generated for each item accepted here. Returns a
        dictionary with the following booleans:

            - accepted: Indicates if the plugin is interested in this value at
                all. Required.
            - enabled: If True, the plugin will be enabled in the UI, otherwise
                it will be disabled. Optional, True by default.
            - visible: If True, the plugin will be visible in the UI, otherwise
                it will be hidden. Optional, True by default.
            - checked: If True, the plugin will be checked in the UI, otherwise
                it will be unchecked. Optional, True by default.

        :param settings: Dictionary of Settings. The keys are strings, matching
                         the keys returned in the settings property. The values
                         are `Setting` instances.
        :param item: Item to process

        :returns: dictionary with boolean keys accepted, required and enabled
        """

        # if a publish template is configured, disable context change. This
        # is a temporary measure until the publisher handles context switching
        # natively.
        if settings.get("Publish Template").value:
            item.context_change_allowed = False

        path = _session_path()

        if not path:
            # the session has not been saved before (no path determined).
            # provide a save button. the session will need to be saved before
            # validation will succeed.
            self.logger.warn(
                "The Harmony session has not been saved.", extra=_get_save_as_action()
            )

        self.logger.info("Harmony '%s' plugin accepted the current session." % (self.name,))
        return {"accepted": True, "checked": True}

    def validate(self, settings, item):
        """
        Validates the given item to check that it is ok to publish. Returns a
        boolean to indicate validity.

        :param settings: Dictionary of Settings. The keys are strings, matching
                         the keys returned in the settings property. The values 
                         are `Setting` instances.
        :param item: Item to process
        :returns: True if item is valid, False otherwise.
        """

        publisher = self.parent
        path = _session_path()

        # ---- ensure the session has been saved

        if not path:
            # the session still requires saving. provide a save button.
            # validation fails.
            error_msg = "The Harmony session has not been saved."
            self.logger.error(error_msg, extra=_get_save_as_action())
            raise Exception(error_msg)

        # ---- check the session against any attached work template

        # get the path in a normalized state. no trailing separator,
        # separators are appropriate for current os, no double separators,
        # etc.
        path = _to_storage_root_path(sgtk.util.ShotgunPath.normalize(path), publisher.sgtk)

        # if the session item has a known work template, see if the path
        # matches. if not, warn the user and provide a way to save the file to
        # a different path
        work_template = item.properties.get("work_template")
        if work_template:
            if not work_template.validate(path):
                self.logger.warning(
                    "The current session does not match the configured work " "file template.",
                    extra={
                        "action_button": {
                            "label": "Save File",
                            "tooltip": "Save the current session to a " "different file name",
                            # will launch wf2 if configured
                            "callback": _get_save_as_action(),
                        }
                    },
                )
            else:
                self.logger.debug("Work template configured and matches session file.")
        else:
            self.logger.debug("No work template configured.")

        # ---- see if the version can be bumped post-publish

        # check to see if the next version of the work file already exists on
        # disk. if so, warn the user and provide the ability to jump to save
        # to that version now
        (next_version_path, version) = self._get_next_version_info(path, item)
        if next_version_path and os.path.exists(next_version_path):

            # determine the next available version_number. just keep asking for
            # the next one until we get one that doesn't exist.
            while os.path.exists(next_version_path):
                (next_version_path, version) = self._get_next_version_info(
                    next_version_path, item
                )

            error_msg = "The next version of this file already exists on disk."
            self.logger.error(
                error_msg,
                extra={
                    "action_button": {
                        "label": "Save to v%s" % (version,),
                        "tooltip": "Save to the next available version number, "
                        "v%s" % (version,),
                        "callback": lambda: _save_session(next_version_path),
                    }
                },
            )
            raise Exception(error_msg)

        # ---- populate the necessary properties and call base class validation

        # populate the publish template on the item if found
        publish_template_setting = settings.get("Publish Template")
        publish_template = publisher.engine.get_template_by_name(
            publish_template_setting.value
        )
        if publish_template:
            item.properties["publish_template"] = publish_template

        # set the session path on the item for use by the base plugin
        # validation
        # step. NOTE: this path could change prior to the publish phase.
        item.properties["path"] = path
        item.properties["publish_path"] = self.get_publish_path(settings, item)

        # The base file publisher rejects existing publishes when templates
        # are present. Harmony sessions support re-publishing the same path,
        # so hide template markers during validation only; the base hook will
        # still warn about conflicts and the real templates are restored for
        # publish/finalize.
        template_properties = {}
        for key in ("work_template", "publish_template"):
            if key in item.properties:
                template_properties[key] = item.properties[key]
                del item.properties[key]

        try:
            return super(HarmonySessionPublishPlugin, self).validate(settings, item)
        finally:
            item.properties.update(template_properties)

    def publish(self, settings, item):
        """
        Executes the publish logic for the given item and settings.

        :param settings: Dictionary of Settings. The keys are strings, matching
                         the keys returned in the settings property.
                         The values are `Setting` instances.
        :param item: Item to process
        """

        # get the path in a normalized state. no trailing separator, separators
        # are appropriate for current os, no double separators, etc.
        source_path = sgtk.util.ShotgunPath.normalize(_session_path())
        path = _to_storage_root_path(source_path, self.parent.sgtk)

        # ensure the session is saved
        _save_session()

        # update the item with the saved session path
        item.properties["path"] = path
        item.properties["harmony_source_path"] = source_path
        if "publish_path" in item.properties:
            del item.properties["publish_path"]
        item.properties["publish_path"] = self.get_publish_path(settings, item)

        # add dependencies for the base class to register when publishing
        item.properties[
            "publish_dependencies"
        ] = _harmony_find_additional_session_dependencies()

        # let the base class register the publish
        super(HarmonySessionPublishPlugin, self).publish(settings, item)
        item.properties.sg_publish_path = _get_publish_local_path(
            item.properties.sg_publish_data, path
        )

    def finalize(self, settings, item):
        """
        Execute the finalization pass. This pass executes once all the publish
        tasks have completed, and can for example be used to version up files.

        :param settings: Dictionary of Settings. The keys are strings, matching
                         the keys returned in the settings property.
                         The values are `Setting` instances.
        :param item: Item to process
        """

        # do the base class finalization
        super(HarmonySessionPublishPlugin, self).finalize(settings, item)

        # bump the session file to the next version
        def save_next_version(path):
            source_path = item.properties.get("harmony_source_path")
            physical_path = _to_physical_path(
                path,
                item.properties["path"],
                source_path,
            )
            _save_session(physical_path)

        self._save_to_next_version(item.properties["path"], item, save_next_version)

    def _copy_work_to_publish(self, settings, item):
        """
        This method handles copying work file path(s) to a designated publish
        location.

        This method requires a "work_template" and a "publish_template" be set
        on the supplied item.

        The method will handle copying the "path" property to the corresponding
        publish location assuming the path corresponds to the "work_template"
        and the fields extracted from the "work_template" are sufficient to
        satisfy the "publish_template".

        The method will not attempt to copy files if any of the above
        requirements are not met. If the requirements are met, the file will
        ensure the publish path folder exists and then copy the file to that
        location.

        If the item has "sequence_paths" set, it will attempt to copy all paths
        assuming they meet the required criteria with respect to the templates.

        """
        publisher = self.parent
        dcc_app = publisher.engine.app

        # ---- ensure templates are available
        work_template = item.properties.get("work_template")
        if not work_template:
            self.logger.debug(
                "No work template set on the item. " "Skipping copy file to publish location."
            )
            return

        publish_template = self.get_publish_template(settings, item)
        if not publish_template:
            self.logger.debug(
                "No publish template set on the item. "
                "Skipping copying file to publish location."
            )
            return

        # ---- get a list of files to be copied

        # by default, the path that was collected for publishing
        work_file = item.properties.path
        source_file = item.properties.get("harmony_source_path") or work_file

        # ---- copy the work files to the publish location
        publish_file = self._get_harmony_publish_path(settings, item, work_file)
        if not publish_file:
            self.logger.warning(
                "Could not resolve a publish path for work file '%s'. "
                "Publishing in place." % (work_file,)
            )
            return

        self.logger.info("Copying Harmony project to publish location:")
        self.logger.info("  %s" % (publish_file,))
        physical_publish_file = _to_physical_path(publish_file, work_file, source_file)
        return dcc_app.save_project_as(
            source_file=source_file, target_file=physical_publish_file, open_project=False
        )

    def get_publish_path(self, settings, item):
        """
        Return the Harmony publish path.

        Harmony projects can be valid Toolkit work files even when the path
        returned by Harmony does not validate against the configured work
        template. In that case, use the current context plus the version in the
        work filename to resolve the publish template instead of publishing in
        place.
        """

        path = item.get_property("path")
        publish_template = self.get_publish_template(settings, item)

        if path and publish_template:
            publish_path = self._get_harmony_publish_path(settings, item, path)
            if publish_path:
                return publish_path

        return super(HarmonySessionPublishPlugin, self).get_publish_path(settings, item)

    def _get_harmony_publish_path(self, settings, item, path):
        """
        Resolve a publish path from the work template or the item context.
        """

        publisher = self.parent
        work_template = item.properties.get("work_template")
        publish_template = self.get_publish_template(settings, item)

        if not publish_template:
            return None

        if work_template and work_template.validate(path):
            fields = work_template.get_fields(path)
        else:
            if work_template:
                self.logger.debug(
                    "Work file '%s' did not match work template '%s'. "
                    "Resolving publish path from context." % (path, work_template)
                )

            try:
                fields = item.context.as_template_fields(publish_template, validate=True)
            except sgtk.TankError:
                ctx_entity = item.context.task or item.context.entity or item.context.project
                if ctx_entity:
                    publisher.sgtk.create_filesystem_structure(
                        ctx_entity["type"], ctx_entity["id"]
                    )
                fields = item.context.as_template_fields(publish_template, validate=True)

            if "version" in publish_template.keys and "version" not in fields:
                version = publisher.util.get_version_number(path)
                if version is not None:
                    fields["version"] = version

        missing_keys = publish_template.missing_keys(fields)
        if missing_keys:
            self.logger.warning(
                "Work file '%s' missing keys required for the publish "
                "template: %s" % (path, missing_keys)
            )
            return None

        publish_path = publish_template.apply_fields(fields)
        self.logger.debug(
            "Resolved Harmony publish path: %s" % (publish_path,)
        )
        return publish_path


def _harmony_find_additional_session_dependencies():
    """
    Find additional dependencies from the session
    """

    return []


def _get_publish_local_path(sg_publish_data, fallback_path=None):
    """
    Return a local filesystem path from a ShotGrid publish path dictionary.
    """

    path_data = sg_publish_data.get("path") or {}
    if not isinstance(path_data, dict):
        return fallback_path

    local_path = path_data.get("local_path")
    if local_path:
        return local_path

    platform_keys = {
        "win32": "local_path_windows",
        "darwin": "local_path_mac",
        "linux2": "local_path_linux",
        "linux": "local_path_linux",
    }

    local_path = path_data.get(platform_keys.get(sys.platform))
    if local_path:
        return local_path

    for key in ("local_path_windows", "local_path_mac", "local_path_linux"):
        local_path = path_data.get(key)
        if local_path:
            return local_path

    return fallback_path


def _to_storage_root_path(path, tk=None):
    """
    Convert Harmony's resolved path back to the configured Toolkit root path.

    Harmony can report a subst drive path as its real filesystem location, for
    example a OneDrive user folder. Toolkit templates and publishes should use
    the configured storage root instead.
    """

    if not path or not tk:
        return path

    try:
        storage_roots = tk.pipeline_configuration.get_data_roots()
    except Exception:
        return path

    try:
        project_disk_name = tk.pipeline_configuration.get_project_disk_name()
    except Exception:
        project_disk_name = None

    normalized_path = sgtk.util.ShotgunPath.normalize(path)
    path_lower = normalized_path.lower()

    for root_path in storage_roots.values():
        if not root_path:
            continue

        root_path = sgtk.util.ShotgunPath.normalize(root_path)
        root_lower = root_path.lower().rstrip("\\/")

        if path_lower == root_lower or path_lower.startswith(root_lower + os.path.sep):
            return normalized_path

        if project_disk_name:
            project_marker = os.path.sep + project_disk_name.lower() + os.path.sep
            marker_index = path_lower.find(project_marker)
            if marker_index != -1:
                relative_path = normalized_path[marker_index + 1 :]
                return sgtk.util.ShotgunPath.normalize(
                    os.path.join(root_path, relative_path)
                )

    return normalized_path


def _to_physical_path(path, reference_storage_path, reference_physical_path):
    """
    Convert a Toolkit-root path back to the physical path Harmony can access.
    """

    if not path or not reference_storage_path or not reference_physical_path:
        return path

    normalized_path = sgtk.util.ShotgunPath.normalize(path)
    storage_path = sgtk.util.ShotgunPath.normalize(reference_storage_path)
    physical_path = sgtk.util.ShotgunPath.normalize(reference_physical_path)

    storage_path_lower = storage_path.lower()
    physical_path_lower = physical_path.lower()

    marker = None
    for storage_part in storage_path.split(os.path.sep):
        if storage_part and ("%s%s" % (os.path.sep, storage_part.lower(), os.path.sep)) in physical_path_lower:
            marker = os.path.sep + storage_part.lower() + os.path.sep
            break

    if marker:
        storage_index = storage_path_lower.find(marker)
        physical_index = physical_path_lower.find(marker)
        target_index = normalized_path.lower().find(marker)
        if storage_index != -1 and physical_index != -1 and target_index != -1:
            physical_root = physical_path[: physical_index + 1]
            relative_path = normalized_path[target_index + 1 :]
            return sgtk.util.ShotgunPath.normalize(
                os.path.join(physical_root, relative_path)
            )

    return normalized_path


def _session_path():
    """
    Return the path to the current session
    :return:
    """
    engine = sgtk.platform.current_engine()

    # get the path to the current file
    path = engine.app.get_current_project_path()

    if isinstance(path, _unicode_type):
        path = path.encode("utf-8")

    return path


def _save_session(path=None):
    """
    Save the current session to the supplied path.
    """

    engine = sgtk.platform.current_engine()
    if path is None:
        engine.app.save_project()
    else:
        # Ensure that the folder is created when saving
        folder = os.path.dirname(path)
        ensure_folder_exists(folder)

        # we are saving a new version, so we only need the name of the file
        _, filename = os.path.split(path)
        filename_file, _ = os.path.splitext(filename)
        engine.app.save_new_version(filename_file)


# TODO: method duplicated in all the Harmony hooks
def _get_save_as_action():
    """
    Simple helper for returning a log action dict for saving the session
    """

    engine = sgtk.platform.current_engine()

    callback = _save_as

    # if workfiles2 is configured, use that for file save
    if "tk-multi-workfiles2" in engine.apps:
        app = engine.apps["tk-multi-workfiles2"]
        if hasattr(app, "show_file_save_dlg"):
            callback = app.show_file_save_dlg

    return {
        "action_button": {
            "label": "Save As...",
            "tooltip": "Save the current session",
            "callback": callback,
        }
    }


def _save_as():
    engine = sgtk.platform.current_engine()
    engine.app.save_new_version_action()
