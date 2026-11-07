// Included after the generated factory and function classes.
struct FunctionObject { PyObject_HEAD nf::Base *state; int id; };
static PyTypeObject FunctionType = {PyVarObject_HEAD_INIT(nullptr, 0)};
static unsigned long long counts[FUNCTION_COUNT] = {};

static void function_free(FunctionObject *self) {
    delete self->state;
    Py_TYPE(self)->tp_free(reinterpret_cast<PyObject *>(self));
}
static PyObject *function_copy(FunctionObject *self, PyObject *) {
    auto *result = reinterpret_cast<FunctionObject *>(FunctionType.tp_alloc(&FunctionType, 0));
    if (!result) return nullptr;
    try { result->state = self->state->clone(); result->id = self->id; }
    catch (const std::exception &e) { Py_DECREF(result); PyErr_SetString(PyExc_RuntimeError,e.what()); return nullptr; }
    return reinterpret_cast<PyObject *>(result);
}
static PyMethodDef function_methods[] = {
    {"__copy__", reinterpret_cast<PyCFunction>(function_copy), METH_NOARGS, nullptr},
    {"__deepcopy__", reinterpret_cast<PyCFunction>(function_copy), METH_O, nullptr},
    {nullptr,nullptr,0,nullptr}
};
static PyObject *function_new(PyObject *, PyObject *args) {
    int id,cap;
    if(!PyArg_ParseTuple(args,"ii",&id,&cap))return nullptr;
    if(id<0||id>=FUNCTION_COUNT||cap<502){PyErr_SetString(PyExc_ValueError,"Invalid native function configuration");return nullptr;}
    auto *result=reinterpret_cast<FunctionObject *>(FunctionType.tp_alloc(&FunctionType,0));
    if(!result)return nullptr;
    try{result->state=make_function(id,cap);result->id=id;}
    catch(const std::exception &e){Py_DECREF(result);PyErr_SetString(PyExc_RuntimeError,e.what());return nullptr;}
    return reinterpret_cast<PyObject *>(result);
}
static PyObject *function_step(PyObject *,PyObject *const *args,Py_ssize_t nargs) {
    if(nargs!=3||Py_TYPE(args[0])!=&FunctionType||!PyTuple_CheckExact(args[2])){
        PyErr_SetString(PyExc_TypeError,"step expects state, bar, tuple");return nullptr;
    }
    auto *self=reinterpret_cast<FunctionObject *>(args[0]);
    long long bar=PyLong_AsLongLong(args[1]);
    if(PyErr_Occurred())return nullptr;
    if(PyTuple_GET_SIZE(args[2])!=input_counts[self->id]){PyErr_SetString(PyExc_ValueError,"Native input arity mismatch");return nullptr;}
    try {
        std::vector<nf::N> values;
        values.reserve(PyTuple_GET_SIZE(args[2]));
        for(Py_ssize_t i=0;i<PyTuple_GET_SIZE(args[2]);++i)values.push_back(nf::from_python(PyTuple_GET_ITEM(args[2],i)));
        auto output=self->state->run(bar,values);
        self->state->last=bar;
        ++counts[self->id];
        PyObject *result=PyTuple_New(output.size());
        if(!result)return nullptr;
        for(size_t i=0;i<output.size();++i){
            PyObject *item=nf::to_python(output[i]);
            if(!item){Py_DECREF(result);return nullptr;}
            PyTuple_SET_ITEM(result,i,item);
        }
        return result;
    } catch(const std::exception &e){if(!PyErr_Occurred())PyErr_SetString(PyExc_RuntimeError,e.what());return nullptr;}
}
static PyObject *function_counts(PyObject *,PyObject *) {
    PyObject *result=PyTuple_New(FUNCTION_COUNT);
    if(!result)return nullptr;
    for(int i=0;i<FUNCTION_COUNT;++i)PyTuple_SET_ITEM(result,i,PyLong_FromUnsignedLongLong(counts[i]));
    return result;
}
static PyMethodDef module_functions[]={
    {"new",function_new,METH_VARARGS,nullptr},
    {"step",reinterpret_cast<PyCFunction>(function_step),METH_FASTCALL,nullptr},
    {"counts",function_counts,METH_NOARGS,nullptr},
    {nullptr,nullptr,0,nullptr}
};
static PyModuleDef function_module={PyModuleDef_HEAD_INIT,FUNCTION_MODULE_NAME,nullptr,-1,module_functions};
PyMODINIT_FUNC FUNCTION_INIT() {
    PyObject *dependencies=PyInit__function_kernel_dependencies();
    if(!dependencies)return nullptr;
    Py_DECREF(dependencies);
    PyObject *na_module=PyImport_ImportModule("pynecore.types.na");
    if(!na_module)return nullptr;
    nf::na_bool_value=PyObject_GetAttrString(na_module,"na_bool");
    Py_DECREF(na_module);
    if(!nf::na_bool_value)return nullptr;
    PyObject *runtime=PyImport_ImportModule("pynecore.core.native_functions");
    if(!runtime)return nullptr;
    nf::legacy_factory=PyObject_GetAttrString(runtime,"restore_indicator");
    Py_DECREF(runtime);
    if(!nf::legacy_factory)return nullptr;
    FunctionType.tp_name=FUNCTION_MODULE_NAME ".Function";
    FunctionType.tp_basicsize=sizeof(FunctionObject);
    FunctionType.tp_flags=Py_TPFLAGS_DEFAULT;
    FunctionType.tp_dealloc=reinterpret_cast<destructor>(function_free);
    FunctionType.tp_methods=function_methods;
    if(PyType_Ready(&FunctionType)<0)return nullptr;
    return PyModule_Create(&function_module);
}
