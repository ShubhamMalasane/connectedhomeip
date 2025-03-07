#
#    Copyright (c) 2022 Project CHIP Authors
#
#    Licensed under the Apache License, Version 2.0 (the 'License');
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an 'AS IS' BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import copy
from dataclasses import dataclass, field
from enum import Enum, auto

import yaml

from . import fixes
from .constraints import get_constraints, is_typed_constraint
from .definitions import SpecDefinitions
from .pics_checker import PICSChecker

_TESTS_SECTION = [
    'name',
    'config',
    'tests',
    'PICS',
]

_TEST_SECTION = [
    'label',
    'cluster',
    'command',
    'disabled',
    'endpoint',
    'identity',
    'fabricFiltered',
    'groupId',
    'verification',
    'nodeId',
    'attribute',
    'optional',
    'PICS',
    'arguments',
    'response',
    'minInterval',
    'maxInterval',
    'timedInteractionTimeoutMs',
    'busyWaitMs',
    'wait',
]

_TEST_ARGUMENTS_SECTION = [
    'values',
    'value',
]

_TEST_RESPONSE_SECTION = [
    'value',
    'values',
    'error',
    'clusterError',
    'constraints',
    'type',
    'hasMasksSet',
    'contains',
    'saveAs'
]

_ATTRIBUTE_COMMANDS = [
    'readAttribute',
    'writeAttribute',
    'subscribeAttribute',
    'waitForReport',
]

_EVENT_COMMANDS = [
    'readEvent',
    'subscribeEvent',
]


class PostProcessCheckStatus(Enum):
    '''Indicates the post processing check step status.'''
    SUCCESS = 'success',
    WARNING = 'warning',
    ERROR = 'error'


class PostProcessCheckType(Enum):
    '''Indicates the post processing check step type.'''
    IM_STATUS = auto()
    CLUSTER_STATUS = auto()
    RESPONSE_VALIDATION = auto()
    CONSTRAINT_VALIDATION = auto()
    SAVE_AS_VARIABLE = auto()
    WAIT_VALIDATION = auto()


class PostProcessCheck:
    '''Information about a single post processing operation that was performed.

    Each check has a helpful message, indicating what the post processing operation did and whether
    it was successful or not.
    '''

    def __init__(self, state: PostProcessCheckStatus, category: PostProcessCheckType, message: str):
        self.state = state
        self.category = category
        self.message = message

    def is_success(self) -> bool:
        return self.state == PostProcessCheckStatus.SUCCESS

    def is_warning(self) -> bool:
        return self.state == PostProcessCheckStatus.WARNING

    def is_error(self) -> bool:
        return self.state == PostProcessCheckStatus.ERROR


class PostProcessResponseResult:
    '''Post processing response result information.

    There are multiple operations that occur when post processing a response. This contains all the
    results for each operation performed. Note that the number and types of steps performed is
    dependant on test step itself.
    '''

    def __init__(self):
        self.entries = []
        self.successes = 0
        self.warnings = 0
        self.errors = 0

    def success(self, category: PostProcessCheckType, message: str):
        '''Adds a success entry that occured when post processing response to results.'''
        self._insert(PostProcessCheckStatus.SUCCESS, category, message)
        self.successes += 1

    def warning(self, category: PostProcessCheckType, message: str):
        '''Adds a warning entry that occured when post processing response to results.'''
        self._insert(PostProcessCheckStatus.WARNING, category, message)
        self.warnings += 1

    def error(self, category: PostProcessCheckType, message: str):
        '''Adds an error entry that occured when post processing response to results.'''
        self._insert(PostProcessCheckStatus.ERROR, category, message)
        self.errors += 1

    def is_success(self):
        # It is possible that post processing a response doesn't have any success entires added
        # that is why we explicitly only search for if an error occurred.
        return self.errors == 0

    def is_failure(self):
        return self.errors != 0

    def _insert(self, state: PostProcessCheckStatus, category: PostProcessCheckType, message: str):
        log = PostProcessCheck(state, category, message)
        self.entries.append(log)


def _check_valid_keys(section, valid_keys_dict):
    if section:
        for key in section:
            if key not in valid_keys_dict:
                raise KeyError(f'Unknown key: {key}')


def _value_or_none(data, key):
    return data[key] if key in data else None


def _value_or_config(data, key, config):
    return data[key] if key in data else config.get(key)


class _TestStepWithPlaceholders:
    '''A single YAML test parsed, as is, from YAML.

    Some YAML test steps contain placeholders for variable subsitution. The value of the variable
    is only known after an earlier test step's has executed and the result successfully post
    processed.
    '''

    def __init__(self, test: dict, config: dict, definitions: SpecDefinitions, pics_checker: PICSChecker):
        # Disabled tests are not parsed in order to allow the test to be added to the test
        # suite even if the feature is not implemented yet.
        self.is_enabled = not ('disabled' in test and test['disabled'])
        if not self.is_enabled:
            return

        self._parsing_config_variable_storage = config

        _check_valid_keys(test, _TEST_SECTION)

        self.label = _value_or_none(test, 'label')
        self.optional = _value_or_none(test, 'optional')
        self.node_id = _value_or_config(test, 'nodeId', config)
        self.group_id = _value_or_config(test, 'groupId', config)
        self.cluster = _value_or_config(test, 'cluster', config)
        self.command = _value_or_config(test, 'command', config)
        self.attribute = _value_or_none(test, 'attribute')
        self.endpoint = _value_or_config(test, 'endpoint', config)
        self.is_pics_enabled = pics_checker.check(_value_or_none(test, 'PICS'))

        self.identity = _value_or_none(test, 'identity')
        self.fabric_filtered = _value_or_none(test, 'fabricFiltered')
        self.min_interval = _value_or_none(test, 'minInterval')
        self.max_interval = _value_or_none(test, 'maxInterval')
        self.timed_interaction_timeout_ms = _value_or_none(
            test, 'timedInteractionTimeoutMs')
        self.busy_wait_ms = _value_or_none(test, 'busyWaitMs')
        self.wait_for = _value_or_none(test, 'wait')

        self.is_attribute = self.command in _ATTRIBUTE_COMMANDS or self.wait_for in _ATTRIBUTE_COMMANDS
        self.is_event = self.command in _EVENT_COMMANDS or self.wait_for in _EVENT_COMMANDS

        self.arguments_with_placeholders = _value_or_none(test, 'arguments')
        self.response_with_placeholders = _value_or_none(test, 'response')

        _check_valid_keys(self.arguments_with_placeholders,
                          _TEST_ARGUMENTS_SECTION)
        _check_valid_keys(self.response_with_placeholders,
                          _TEST_RESPONSE_SECTION)

        self._convert_single_value_to_values(self.arguments_with_placeholders)
        self._convert_single_value_to_values(self.response_with_placeholders)

        argument_mapping = None
        response_mapping = None
        response_mapping_name = None

        if self.is_attribute:
            attribute = definitions.get_attribute_by_name(
                self.cluster, self.attribute)
            if attribute:
                attribute_mapping = self._as_mapping(definitions, self.cluster,
                                                     attribute.definition.data_type.name)
                argument_mapping = attribute_mapping
                response_mapping = attribute_mapping
                response_mapping_name = attribute.definition.data_type.name
        else:
            command = definitions.get_command_by_name(
                self.cluster, self.command)
            if command:
                argument_mapping = self._as_mapping(
                    definitions, self.cluster, command.input_param)
                response_mapping = self._as_mapping(
                    definitions, self.cluster, command.output_param)
                response_mapping_name = command.output_param

        self.argument_mapping = argument_mapping
        self.response_mapping = response_mapping
        self.response_mapping_name = response_mapping_name
        self.update_arguments(self.arguments_with_placeholders)
        self.update_response(self.response_with_placeholders)

        # This performs a very basic sanity parse time check of constraints. This parsing happens
        # again inside post processing response since at that time we will have required variables
        # to substitute in. This parsing check here has value since some test can take a really
        # long time to run so knowing earlier on that the test step would have failed at parsing
        # time before the test step run occurs save developer time that building yaml tests.
        if self.response_with_placeholders:
            for value in self.response_with_placeholders['values']:
                if 'constraints' not in value:
                    continue
                get_constraints(value['constraints'])

    def _convert_single_value_to_values(self, container):
        if container is None or 'values' in container:
            return

        # Attribute tests pass a single value argument that does not carry a name but
        # instead uses a generic 'value' keyword. Convert to keyword to be the single
        # members of the 'values' array which is what is used for other tests.
        value = {}

        known_keys_to_copy = ['value', 'constraints', 'saveAs']
        known_keys_to_allow = ['error', 'clusterError']

        for key, item in list(container.items()):
            if key in known_keys_to_copy:
                value[key] = item
                del container[key]
            elif key in known_keys_to_allow:
                # Nothing to do for those keys.
                pass
            else:
                raise KeyError(f'Unknown key: {key}')

        container['values'] = [value]

    def _as_mapping(self, definitions, cluster_name, target_name):
        element = definitions.get_type_by_name(cluster_name, target_name)

        if hasattr(element, 'base_type'):
            target_name = element.base_type.lower()
        elif hasattr(element, 'fields'):
            target_name = {f.name: self._as_mapping(
                definitions, cluster_name, f.data_type.name) for f in element.fields}
        elif target_name:
            target_name = target_name.lower()

        return target_name

    def update_arguments(self, arguments_with_placeholders):
        self._update_with_definition(
            arguments_with_placeholders, self.argument_mapping)

    def update_response(self, response_with_placeholders):
        self._update_with_definition(
            response_with_placeholders, self.response_mapping)

    def _update_with_definition(self, container: dict, mapping_type):
        if not container or not mapping_type:
            return

        for value in list(container['values']):
            for key, item_value in list(value.items()):
                if self.is_attribute:
                    mapping = mapping_type
                else:
                    target_key = value['name']
                    if mapping_type.get(target_key) is None:
                        for candidate_key in mapping_type:
                            if candidate_key.lower() == target_key.lower():
                                raise KeyError(
                                    f'"{self.label}": Unknown key: "{target_key}". Did you mean "{candidate_key}" ?')
                        raise KeyError(
                            f'"{self.label}": Unknown key: "{target_key}". Candidates are: "{[ key for key in mapping_type]}".')
                    mapping = mapping_type[target_key]

                if key == 'value':
                    value[key] = self._update_value_with_definition(
                        item_value, mapping)
                elif key == 'saveAs' and type(item_value) is str and item_value not in self._parsing_config_variable_storage:
                    self._parsing_config_variable_storage[item_value] = None
                elif key == 'constraints':
                    for constraint, constraint_value in item_value.items():
                        # Only apply update_value_with_definition to constraints that have a value that depends on
                        # the the value type for the target field.
                        if is_typed_constraint(constraint):
                            value[key][constraint] = self._update_value_with_definition(
                                constraint_value, mapping_type)
                else:
                    # This key, value pair does not rely on cluster specifications.
                    pass

    def _update_value_with_definition(self, value, mapping_type):
        if not mapping_type:
            return value

        if type(value) is dict:
            rv = {}
            for key in value:
                # FabricIndex is a special case where the framework requires it to be passed even
                # if it is not part of the requested arguments per spec and not part of the XML
                # definition.
                if key == 'FabricIndex' or key == 'fabricIndex':
                    rv[key] = value[key]  # int64u
                else:
                    mapping = mapping_type[key]
                    rv[key] = self._update_value_with_definition(
                        value[key], mapping)
            return rv
        if type(value) is list:
            return [self._update_value_with_definition(entry, mapping_type) for entry in value]
        # TODO currently unsure if the check of `value not in config` is sufficant. For
        # example let's say value = 'foo + 1' and map type is 'int64u', we would arguably do
        # the wrong thing below.
        if value is not None and value not in self._parsing_config_variable_storage:
            if mapping_type == 'int64u' or mapping_type == 'int64s' or mapping_type == 'bitmap64' or mapping_type == 'epoch_us':
                value = fixes.try_apply_float_to_integer_fix(value)
                value = fixes.try_apply_yaml_cpp_longlong_limitation_fix(value)
                value = fixes.try_apply_yaml_unrepresentable_integer_for_javascript_fixes(
                    value)
            elif mapping_type == 'single' or mapping_type == 'double':
                value = fixes.try_apply_yaml_float_written_as_strings(value)
            elif isinstance(value, float) and mapping_type != 'single' and mapping_type != 'double':
                value = fixes.try_apply_float_to_integer_fix(value)
            elif mapping_type == 'octet_string' or mapping_type == 'long_octet_string':
                value = fixes.convert_yaml_octet_string_to_bytes(value)
            elif mapping_type == 'boolean':
                value = bool(value)

        return value


class TestStep:
    '''A single YAML test action parsed from YAML.

    This object contains all the information required for a test runner to execute the test step.
    It also provide a function that is expected to be called by the test runner to post process
    the recieved response from the accessory. Post processing both validates recieved response
    and saves any variables that might be required but test step that have yet to be executed.
    '''

    def __init__(self, test: _TestStepWithPlaceholders, runtime_config_variable_storage: dict):
        self._test = test
        self._runtime_config_variable_storage = runtime_config_variable_storage
        self.arguments = copy.deepcopy(test.arguments_with_placeholders)
        self.response = copy.deepcopy(test.response_with_placeholders)
        if test.is_pics_enabled:
            self._update_placeholder_values(self.arguments)
            self._update_placeholder_values(self.response)
            self._test.node_id = self._config_variable_substitution(
                self._test.node_id)
            test.update_arguments(self.arguments)
            test.update_response(self.response)

    @property
    def is_enabled(self):
        return self._test.is_enabled

    @property
    def is_pics_enabled(self):
        return self._test.is_pics_enabled

    @property
    def is_attribute(self):
        return self._test.is_attribute

    @property
    def is_event(self):
        return self._test.is_event

    @property
    def label(self):
        return self._test.label

    @property
    def optional(self):
        return self._test.optional

    @property
    def node_id(self):
        return self._test.node_id

    @property
    def group_id(self):
        return self._test.group_id

    @property
    def cluster(self):
        return self._test.cluster

    @property
    def command(self):
        return self._test.command

    @property
    def attribute(self):
        return self._test.attribute

    @property
    def endpoint(self):
        return self._test.endpoint

    @property
    def identity(self):
        return self._test.identity

    @property
    def fabric_filtered(self):
        return self._test.fabric_filtered

    @property
    def min_interval(self):
        return self._test.min_interval

    @property
    def max_interval(self):
        return self._test.max_interval

    @property
    def timed_interaction_timeout_ms(self):
        return self._test.timed_interaction_timeout_ms

    @property
    def busy_wait_ms(self):
        return self._test.busy_wait_ms

    @property
    def wait_for(self):
        return self._test.wait_for

    def post_process_response(self, response: dict):
        result = PostProcessResponseResult()

        if self.wait_for is not None:
            self._response_cluster_wait_validation(response, result)
            return result

        if self._skip_post_processing(response, result):
            return result

        self._response_error_validation(response, result)
        if self.response:
            self._response_cluster_error_validation(response, result)
            self._response_values_validation(response, result)
            self._response_constraints_validation(response, result)
            self._maybe_save_as(response, result)

        return result

    def _response_cluster_wait_validation(self, response, result):
        """Check if the response concrete path matches the configuration of the test step
           and validate that the response type (e.g readAttribute/writeAttribute/...) matches
           the expectation from the test step."""
        check_type = PostProcessCheckType.WAIT_VALIDATION
        error_success = 'The test expectation "{wait_for}" for "{cluster}.{wait_type}" on endpoint {endpoint} is true'
        error_failure = 'The test expectation "{expected} == {received}" is false'

        if self.is_attribute:
            expected_wait_type = self.attribute
            received_wait_type = response.get('attribute')
        elif self.is_event:
            expected_wait_type = self.event
            received_wait_type = response.get('event')
        else:
            expected_wait_type = self.command
            received_wait_type = response.get('command')

        expected_values = [
            self.wait_for,
            self.endpoint,
            # TODO The name in tests does not always use spaces
            self.cluster.replace(' ', ''),
            expected_wait_type
        ]

        received_values = [
            response.get('wait_for'),
            response.get('endpoint'),
            response.get('cluster'),
            received_wait_type
        ]

        success = True
        for expected_value in expected_values:
            received_value = received_values.pop(0)

            if expected_value != received_value:
                result.error(check_type, error_failure.format(
                    expected=expected_value, received=received_value))
                success = False

        if success:
            result.success(check_type, error_success.format(
                wait_for=self.wait_for, cluster=self.cluster, wait_type=expected_wait_type, endpoint=self.endpoint))

    def _skip_post_processing(self, response: dict, result) -> bool:
        '''Should we skip perform post processing.

        Currently we only skip post processing if the test step indicates that sent test step
        invokation was expected to be optionally supported. We confirm that it is optional
        supported by either validating we got the expected error only then indicate that all
        other post processing should be skipped.
        '''
        if not self.optional:
            return False

        received_error = response.get('error', None)
        if received_error is None:
            return False

        if received_error == 'UNSUPPORTED_ATTRIBUTE' or received_error == 'UNSUPPORTED_COMMAND':
            # result.warning(PostProcessCheckType.Optional, f'The response contains the error: "{error}".')
            return True

        return False

    def _response_error_validation(self, response, result):
        check_type = PostProcessCheckType.IM_STATUS
        error_success = 'The test expects the "{error}" error which occured successfully.'
        error_success_no_error = 'The test expects no error and no error occurred.'
        error_wrong_error = 'The test expects the "{error}" error but the "{value}" error occured.'
        error_unexpected_error = 'The test expects no error but the "{error}" error occured.'
        error_unexpected_success = 'The test expects the "{error}" error but no error occured.'

        expected_error = self.response.get('error') if self.response else None

        received_error = response.get('error')

        if expected_error and received_error and expected_error == received_error:
            result.success(check_type, error_success.format(
                error=expected_error))
        elif expected_error and received_error:
            result.error(check_type, error_wrong_error.format(
                error=expected_error, value=received_error))
        elif expected_error and not received_error:
            result.error(check_type, error_unexpected_success.format(
                error=expected_error))
        elif not expected_error and received_error:
            result.error(check_type, error_unexpected_error.format(
                error=received_error))
        elif not expected_error and not received_error:
            result.success(check_type, error_success_no_error)
        else:
            # This should not happens
            raise AssertionError('This should not happens.')

    def _response_cluster_error_validation(self, response, result):
        check_type = PostProcessCheckType.CLUSTER_STATUS
        error_success = 'The test expects the "{error}" error which occured successfully.'
        error_unexpected_success = 'The test expects the "{error}" error but no error occured.'
        error_wrong_error = 'The test expects the "{error}" error but the "{value}" error occured.'

        expected_error = self.response.get('clusterError')
        received_error = response.get('clusterError')

        if expected_error:
            if received_error and expected_error == received_error:
                result.success(check_type, error_success.format(
                    error=expected_error))
            elif received_error:
                result.error(check_type, error_wrong_error.format(
                    error=expected_error, value=received_error))
            else:
                result.error(check_type, error_unexpected_success.format(
                    error=expected_error))
        else:
            # Nothing is logged here to not be redundant with the generic error checking code.
            pass

    def _response_values_validation(self, response, result):
        check_type = PostProcessCheckType.RESPONSE_VALIDATION
        error_success = 'The test expectation "{name} == {value}" is true'
        error_failure = 'The test expectation "{name} == {value}" is false'
        error_name_does_not_exist = 'The test expects a value named "{name}" but it does not exists in the response."'

        for value in self.response['values']:
            if 'value' not in value:
                continue

            expected_name = 'value'
            received_value = response.get('value')
            if not self.is_attribute:
                expected_name = value.get('name')
                if received_value is None or expected_name not in received_value:
                    result.error(check_type, error_name_does_not_exist.format(
                        name=expected_name))
                    continue

                received_value = received_value.get(
                    expected_name) if received_value else None

            expected_value = value.get('value')
            if self._response_value_validation(expected_value, received_value):
                result.success(check_type, error_success.format(
                    name=expected_name, value=expected_value))
            else:
                result.error(check_type, error_failure.format(
                    name=expected_name, value=expected_value))

    def _response_value_validation(self, expected_value, received_value):
        if isinstance(expected_value, list):
            if len(expected_value) != len(received_value):
                return False

            for index, expected_item in enumerate(expected_value):
                received_item = received_value[index]
                if not self._response_value_validation(expected_item, received_item):
                    return False
            return True
        elif isinstance(expected_value, dict):
            for key, expected_item in expected_value.items():
                received_item = received_value.get(key)
                if not self._response_value_validation(expected_item, received_item):
                    return False
            return True
        else:
            return expected_value == received_value

    def _response_constraints_validation(self, response, result):
        check_type = PostProcessCheckType.CONSTRAINT_VALIDATION
        error_success = 'Constraints check passed'
        error_failure = 'Constraints check failed'

        response_type_name = self._test.response_mapping_name
        for value in self.response['values']:
            if 'constraints' not in value:
                continue

            received_value = response.get('value')
            if not self.is_attribute:
                expected_name = value.get('name')
                if received_value is None or expected_name not in received_value:
                    received_value = None
                else:
                    received_value = received_value.get(
                        expected_name) if received_value else None

                if self._test.response_mapping:
                    response_type_name = self._test.response_mapping.get(
                        expected_name)
                else:
                    # We don't have a mapping for this type. This happens for pseudo clusters.
                    # If there is a constraint check for the type it is likely an incorrect
                    # constraint check by the test writter.
                    response_type_name = None

            constraints = get_constraints(value['constraints'])

            if all([constraint.is_met(received_value, response_type_name) for constraint in constraints]):
                result.success(check_type, error_success)
            else:
                # TODO would be helpful to be more verbose here
                result.error(check_type, error_failure)

    def _maybe_save_as(self, response, result):
        check_type = PostProcessCheckType.SAVE_AS_VARIABLE
        error_success = 'The test save the value "{value}" as {name}.'
        error_name_does_not_exist = 'The test expects a value named "{name}" but it does not exists in the response."'

        for value in self.response['values']:
            if 'saveAs' not in value:
                continue

            received_value = response.get('value')
            if not self.is_attribute:
                expected_name = value.get('name')
                if received_value is None or expected_name not in received_value:
                    result.error(check_type, error_name_does_not_exist.format(
                        name=expected_name))
                    continue

                received_value = received_value.get(
                    expected_name) if received_value else None

            save_as = value.get('saveAs')
            self._runtime_config_variable_storage[save_as] = received_value
            result.success(check_type, error_success.format(
                value=received_value, name=save_as))

    def _update_placeholder_values(self, container):
        if not container:
            return

        values = container['values']

        for idx, item in enumerate(values):
            if 'value' in item:
                values[idx]['value'] = self._config_variable_substitution(
                    item['value'])

            if 'constraints' in item:
                for constraint, constraint_value in item['constraints'].items():
                    values[idx]['constraints'][constraint] = self._config_variable_substitution(
                        constraint_value)

        container['values'] = values

    def _config_variable_substitution(self, value):
        if type(value) is list:
            return [self._config_variable_substitution(entry) for entry in value]
        elif type(value) is dict:
            mapped_value = {}
            for key in value:
                mapped_value[key] = self._config_variable_substitution(
                    value[key])
            return mapped_value
        elif type(value) is str:
            # For most tests, a single config variable is used and it can be replaced as in.
            # But some other tests were relying on the fact that the expression was put 'as if' in
            # the generated code and was resolved before being sent over the wire. For such
            # expressions (e.g 'myVar + 1') we need to compute it before sending it over the wire.
            tokens = value.split()
            if len(tokens) == 0:
                return value

            substitution_occured = False
            for idx, token in enumerate(tokens):
                if token in self._runtime_config_variable_storage:
                    variable_info = self._runtime_config_variable_storage[token]
                    if type(variable_info) is dict and 'defaultValue' in variable_info:
                        variable_info = variable_info['defaultValue']
                    if variable_info is not None:
                        tokens[idx] = variable_info
                        substitution_occured = True

            if len(tokens) == 1:
                return tokens[0]

            tokens = [str(token) for token in tokens]
            value = ' '.join(tokens)
            # TODO we should move away from eval. That will mean that we will need to do extra
            # parsing, but it would be safer then just blindly running eval.
            return value if not substitution_occured else eval(value)
        else:
            return value


class YamlTests:
    '''Parses YAML tests and becomes an iterator to provide 'TestStep's

    The provided TestStep is expected to be used by a runner/adapter to run the test step and
    provide the response from the device to the TestStep object.

    Currently this is a one time use object. Eventually this should be refactored to take a
    runner/adapter as an argument and run through all test steps and should be reusable for
    multiple runs.
    '''

    def __init__(self, parsing_config_variable_storage: dict, definitions: SpecDefinitions, pics_checker: PICSChecker, tests: dict):
        self._parsing_config_variable_storage = parsing_config_variable_storage
        enabled_tests = []
        for test in tests:
            test_with_placeholders = _TestStepWithPlaceholders(
                test, self._parsing_config_variable_storage, definitions, pics_checker)
            if test_with_placeholders.is_enabled:
                enabled_tests.append(test_with_placeholders)
        fixes.try_update_yaml_node_id_test_runner_state(
            enabled_tests, self._parsing_config_variable_storage)

        self._runtime_config_variable_storage = copy.deepcopy(
            parsing_config_variable_storage)
        self._tests = enabled_tests
        self._index = 0
        self.count = len(self._tests)

    def __iter__(self):
        return self

    def __next__(self) -> TestStep:
        if self._index < self.count:
            test = self._tests[self._index]
            test_step = TestStep(test, self._runtime_config_variable_storage)
            self._index += 1
            return test_step

        raise StopIteration


@dataclass
class TestParserConfig:
    pics: str = None
    definitions: SpecDefinitions = None
    config_override: dict = field(default_factory=dict)


class TestParser:
    def __init__(self, test_file: str, parser_config: TestParserConfig = TestParserConfig()):
        data = self.__load_yaml(test_file)

        _check_valid_keys(data, _TESTS_SECTION)

        self.name = _value_or_none(data, 'name')
        self.PICS = _value_or_none(data, 'PICS')

        config = data.get('config', {})
        for key, value in parser_config.config_override.items():
            if value is None:
                continue

            if isinstance(config[key], dict) and 'defaultValue' in config[key]:
                config[key]['defaultValue'] = value
            else:
                config[key] = value
        self._parsing_config_variable_storage = config

        # These are a list of "KnownVariables". These are defaults the codegen used to use. This
        # is added for legacy support of tests that expect to uses these "defaults".
        self.__populate_default_config_if_missing('nodeId', 0x12345)
        self.__populate_default_config_if_missing('endpoint', '')
        self.__populate_default_config_if_missing('cluster', '')
        self.__populate_default_config_if_missing('timeout', '90')

        pics_checker = PICSChecker(parser_config.pics)
        tests = _value_or_none(data, 'tests')
        self.tests = YamlTests(
            self._parsing_config_variable_storage, parser_config.definitions, pics_checker, tests)

    def __populate_default_config_if_missing(self, key, value):
        if key not in self._parsing_config_variable_storage:
            self._parsing_config_variable_storage[key] = value

    def __load_yaml(self, test_file):
        with open(test_file) as f:
            loader = yaml.FullLoader
            loader = fixes.try_add_yaml_support_for_scientific_notation_without_dot(
                loader)

            return yaml.load(f, Loader=loader)
        return None
